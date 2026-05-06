// ── State ──────────────────────────────────────────────────────────────────────
const charts = {};
let currentPage = 'dashboard';
let journalPage = 1;
let symbolList  = [];

// ── Navigation ─────────────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page-view').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  currentPage = name;
  if (name === 'dashboard') loadDashboard();
  if (name === 'journal')   { loadSymbols(); journalLoad(1); }
  if (name === 'deep')      loadDeep();
  if (name === 'edge')      loadEdge();
  if (name === 'import')    loadImportLog();
}

// ── Helpers ────────────────────────────────────────────────────────────────────
const fmt   = v => v == null ? '—' : Number(v).toFixed(4);
const fmtC  = v => v == null ? '—' : Number(v).toFixed(2);
const pnlClass = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const pnlSign  = v => v > 0 ? '+' : '';
function durFmt(mins) {
  if (mins == null) return '—';
  if (mins < 60) return mins + 'm';
  if (mins < 1440) return Math.round(mins/60) + 'h ' + (mins%60) + 'm';
  return Math.floor(mins/1440) + 'd ' + Math.floor((mins%1440)/60) + 'h';
}
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}

// ── Chart helpers ─────────────────────────────────────────────────────────────
const chartDefaults = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { labels: { color: '#7986cb', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#7986cb', font: { size: 10 } }, grid: { color: 'rgba(45,50,80,.6)' } },
    y: { ticks: { color: '#7986cb', font: { size: 10 } }, grid: { color: 'rgba(45,50,80,.6)' } }
  }
};
function makeChart(id, type, data, extraOpts={}) {
  if (charts[id]) charts[id].destroy();
  const ctx = document.getElementById(id).getContext('2d');
  charts[id] = new Chart(ctx, { type, data,
    options: Object.assign({}, chartDefaults, extraOpts) });
}

// ══════════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function loadDashboard() {
  const res = await api('/api/dashboard/kpis');
  if (!res.ok) return;
  const d = res.data;

  // Market Pulse (non-blocking, loads in parallel)
  api('/api/market/context?symbols=BTCUSDT').then(mr => {
    const el = document.getElementById('market-pulse');
    if (!mr.ok || !el) return;
    const fg  = mr.data.fear_greed || {};
    const btc = (mr.data.symbols || {})['BTCUSDT'] || {};
    const fr  = btc.funding    || {};
    const ls  = btc.long_short || {};
    const parts = [];
    if (fg.ok) {
      const fgColor = fg.value <= 25 ? 'var(--accent3)' : fg.value <= 45 ? 'var(--accent3)' :
                      fg.value <= 55 ? 'var(--muted)'   : fg.value <= 75 ? 'var(--yellow)' : 'var(--red)';
      parts.push(`<span>😨 Fear &amp; Greed: <strong style="color:${fgColor}">${fg.value} — ${fg.classification}</strong></span>`);
    }
    const bd = mr.data.btc_dominance || {};
    if (bd.ok) {
      const arrow = bd.change_24h >= 0 ? '↑' : '↓';
      const bdColor = bd.change_24h >= 0 ? 'var(--red)' : 'var(--accent3)';  // rising dom = bad for alts
      parts.push(`<span>🏆 BTC Dom: <strong style="color:${bdColor}">${bd.btc_dominance}% ${arrow}${Math.abs(bd.change_24h)}%</strong></span>`);
    }
    if (fr.ok) {
      const frColor = fr.rate > 0 ? 'var(--red)' : 'var(--accent3)';
      const frFlag  = fr.high ? ' ⚠' : '';
      parts.push(`<span>📈 BTC Funding: <strong style="color:${frColor}">${fr.rate_pct > 0 ? '+' : ''}${fr.rate_pct}% (${fr.direction})${frFlag}</strong></span>`);
    }
    if (ls.ok) {
      const lsColor = ls.long_pct > 65 ? 'var(--yellow)' : ls.short_pct > 65 ? 'var(--yellow)' : 'var(--muted)';
      parts.push(`<span>⚖ BTC L/S: <strong style="color:${lsColor}">${ls.long_pct}% long / ${ls.short_pct}% short</strong></span>`);
    }
    if (parts.length) {
      el.innerHTML = parts.join('<span style="color:var(--border)">|</span>');
      el.style.display = 'flex';
    }
  });

  document.getElementById('dash-subtitle').textContent =
    `${d.total_trades} closed positions · Win rate ${d.win_rate}% · Profit factor ${d.profit_factor ?? '—'}`;

  // KPI cards
  const kpis = [
    { label: 'Total Realized P&L', value: (d.total_pnl >= 0 ? '+' : '') + fmtC(d.total_pnl) + ' USDT',
      cls: pnlClass(d.total_pnl), sub: `Net after fees` },
    { label: 'Total Fees', value: fmtC(d.total_fees) + ' USDT', cls: 'neg', sub: 'Paid to exchange' },
    { label: 'Win Rate', value: d.win_rate + '%', cls: d.win_rate >= 50 ? 'pos' : 'neg',
      sub: `${d.win_trades}W / ${d.loss_trades}L` },
    { label: 'Profit Factor', value: d.profit_factor ?? '—', cls: d.profit_factor > 1 ? 'pos' : 'neg',
      sub: 'Gross wins / losses' },
    { label: 'Best Trade', value: '+' + fmtC(d.best_trade) + ' USDT', cls: 'pos' },
    { label: 'Worst Trade', value: fmtC(d.worst_trade) + ' USDT', cls: 'neg' },
    { label: 'Avg Win', value: '+' + fmtC(d.avg_win) + ' USDT', cls: 'pos' },
    { label: 'Avg Loss', value: fmtC(d.avg_loss) + ' USDT', cls: 'neg' },
    { label: 'Max Drawdown', value: fmtC(d.max_drawdown) + ' USDT', cls: 'neg',
      sub: 'Peak-to-trough on PnL curve' },
    { label: 'Total Trades', value: d.total_trades, cls: 'neu' },
  ];
  document.getElementById('kpi-grid').innerHTML = kpis.map(k => `
    <div class="kpi-card">
      <div class="kpi-label">${k.label}</div>
      <div class="kpi-value ${k.cls||''}">${k.value}</div>
      ${k.sub ? `<div class="kpi-sub">${k.sub}</div>` : ''}
    </div>`).join('');

  // Open position risk (async — non-blocking)
  api('/api/live/positions').then(lr => {
    if (!lr.ok) return;
    const pos = lr.data.positions || [];
    const eq  = parseFloat(lr.data.equity?.accountEquity || 0);

    // Use SL-based risk when a stop-loss is set, otherwise fall back to margin
    const totalRisk = pos.reduce((s, p) => {
      const entry = parseFloat(p.entry_price || 0);
      const sl    = parseFloat(p.stop_loss  || 0);
      const size  = parseFloat(p.size_usdt  || 0);
      if (sl > 0 && entry > 0 && size > 0) {
        const slRisk = p.direction === 'Long'
          ? (entry - sl) / entry * size
          : (sl - entry) / entry * size;
        return s + Math.max(0, slRisk);
      }
      return s + (p.margin_usdt || 0); // no SL set — show margin as worst-case
    }, 0);

    const riskPct = eq > 0 ? (totalRisk / eq * 100).toFixed(1) : 0;
    const hasSl   = pos.some(p => parseFloat(p.stop_loss || 0) > 0);
    const el = document.createElement('div');
    el.className = 'kpi-card';
    el.innerHTML = `
      <div class="kpi-label">Open Position Risk</div>
      <div class="kpi-value ${totalRisk > 0 ? 'neg' : 'neu'}">${fmtC(totalRisk)} USDT</div>
      <div class="kpi-sub">${riskPct}% of equity · ${pos.length} open${hasSl ? ' · SL-based' : ' · no SL'}</div>`;
    document.getElementById('kpi-grid').appendChild(el);
  }).catch(() => {});

  // Streak display
  const streakEl = document.getElementById('dash-streak-display');
  if (streakEl) {
    if (d.current_win_streak > 0) {
      streakEl.innerHTML = `<span class="chip-streak-w">🔥 ${d.current_win_streak} WIN STREAK</span>`;
    } else if (d.current_loss_streak > 0) {
      streakEl.innerHTML = `<span class="chip-streak-l">❄ ${d.current_loss_streak} LOSS STREAK</span>`;
    } else {
      streakEl.textContent = 'No active streak';
    }
  }

  // Monthly target tracker
  updateMonthlyTargetUI(d.current_month_pnl ?? 0);

  // PnL curve chart
  if (d.pnl_curve.length) {
    // Sample to max 200 points for performance
    const step = Math.max(1, Math.floor(d.pnl_curve.length / 200));
    const curve = d.pnl_curve.filter((_, i) => i % step === 0);
    makeChart('pnlCurveChart', 'line', {
      labels: curve.map(p => p.date),
      datasets: [{
        label: 'Cumulative P&L (USDT)',
        data: curve.map(p => p.cumulative_pnl),
        borderColor: '#6c63ff', backgroundColor: 'rgba(108,99,255,.1)',
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2
      }]
    }, { plugins: { legend: { display: false } } });
  }

  // Wallet chart
  if (d.wallet_curve.length) {
    makeChart('walletChart', 'line', {
      labels: d.wallet_curve.map(p => p.date.slice(0,10)),
      datasets: [{
        label: 'Wallet Balance (USDT)',
        data: d.wallet_curve.map(p => p.wallet_balance),
        borderColor: '#4fc3f7', backgroundColor: 'rgba(79,195,247,.08)',
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2
      }]
    }, { plugins: { legend: { display: false } } });
  }

  // Top symbols bar chart
  if (d.top_symbols.length) {
    const colors = d.top_symbols.map(s => s.total_pnl >= 0 ? '#26d96b' : '#ef5350');
    makeChart('topSymbolsChart', 'bar', {
      labels: d.top_symbols.map(s => s.symbol),
      datasets: [{
        label: 'Realized P&L (USDT)',
        data: d.top_symbols.map(s => s.total_pnl),
        backgroundColor: colors
      }]
    });
  }

  // Win/Loss donut
  makeChart('winLossChart', 'doughnut', {
    labels: ['Wins', 'Losses'],
    datasets: [{
      data: [d.win_trades, d.loss_trades],
      backgroundColor: ['#26d96b', '#ef5350'], borderWidth: 0
    }]
  }, { scales: undefined,
       plugins: { legend: { position: 'bottom', labels: { color: '#7986cb' } } } });

  // Recent trades table
  document.getElementById('recent-tbody').innerHTML = d.recent_trades.map(t => `
    <tr>
      <td><strong>${t.symbol}</strong></td>
      <td><span class="badge ${t.direction.toLowerCase()}">${t.direction}</span></td>
      <td>${t.open_time?.slice(0,10)}</td>
      <td>${t.close_time?.slice(0,10)}</td>
      <td>${durFmt(t.duration_minutes)}</td>
      <td>${fmtC(t.size_usdt)}</td>
      <td class="${pnlClass(t.realized_pnl)}">${pnlSign(t.realized_pnl)}${fmtC(t.realized_pnl)}</td>
      <td class="neg">${fmtC(t.total_fees)}</td>
    </tr>`).join('');

  // Totals footer
  const totalPnl  = d.recent_trades.reduce((s, t) => s + (t.realized_pnl || 0), 0);
  const totalFees = d.recent_trades.reduce((s, t) => s + (t.total_fees  || 0), 0);
  document.getElementById('recent-tfoot').innerHTML = `
    <tr style="border-top:1px solid var(--border);font-weight:600;font-size:.82rem;color:var(--muted)">
      <td colspan="6" style="text-align:right;padding-right:12px">Total (last ${d.recent_trades.length})</td>
      <td class="${pnlClass(totalPnl)}">${pnlSign(totalPnl)}${fmtC(totalPnl)}</td>
      <td class="neg">${fmtC(totalFees)}</td>
    </tr>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// JOURNAL
// ══════════════════════════════════════════════════════════════════════════════
async function loadSymbols() {
  const res = await api('/api/symbols');
  if (!res.ok) return;
  symbolList = res.data;
  const sel = document.getElementById('j-symbol');
  sel.innerHTML = '<option value="">All Symbols</option>' +
    symbolList.map(s => `<option>${s}</option>`).join('');
}

async function journalLoad(page) {
  journalPage = page;
  const params = new URLSearchParams({
    page,
    per_page: 50,
    search:    document.getElementById('j-search').value,
    symbol:    document.getElementById('j-symbol').value,
    direction: document.getElementById('j-direction').value,
    pnl_side:  document.getElementById('j-pnl').value,
    setup:     document.getElementById('j-setup').value,
    date_from: document.getElementById('j-from').value,
    date_to:   document.getElementById('j-to').value,
  });
  const res = await api('/api/positions?' + params);
  if (!res.ok) return;
  const { positions, total, pages } = res.data;

  document.getElementById('journal-count').textContent = `${total} trade${total !== 1 ? 's' : ''}`;
  document.getElementById('journal-tbody').innerHTML = positions.map(t => `
    <tr onclick="openNotesModal(${t.id},'${escHtml(t.notes||'')}','${escHtml(t.tags||'')}','${escHtml(t.analyst||'')}','${escHtml(t.setup_type||'')}','${escHtml(t.execution_grade||'')}','${escHtml(t.execution_grade_reason||'')}',${t.call_id||'null'})"
        style="cursor:pointer" title="Click to edit">
      <td><strong>${t.symbol}</strong></td>
      <td><span class="badge ${t.direction.toLowerCase()}">${t.direction}</span></td>
      <td>${t.open_time?.slice(0,16)}</td>
      <td>${t.close_time?.slice(0,16)}</td>
      <td>${durFmt(t.duration_minutes)}</td>
      <td>${t.entry_price != null ? fmt(t.entry_price) : '—'}</td>
      <td>${t.close_price != null ? fmt(t.close_price) : '—'}</td>
      <td>${fmtC(t.size_usdt)}</td>
      <td class="${pnlClass(t.realized_pnl)}">${pnlSign(t.realized_pnl)}${fmtC(t.realized_pnl)}</td>
      <td class="neg">${fmtC(t.total_fees)}</td>
      <td onclick="event.stopPropagation()" style="white-space:nowrap">
        ${t.execution_grade
          ? `<span class="grade-badge grade-${t.execution_grade}" title="${escHtml(t.execution_grade_reason||'')}">${t.execution_grade}</span>`
          : ''}
        <button class="btn btn-sm" style="padding:3px 8px;font-size:.7rem;background:rgba(108,99,255,.15);color:var(--accent);border:1px solid rgba(108,99,255,.3)"
          onclick="gradePosition(${t.id}, this)">⚡ Grade</button>
      </td>
      <td style="font-size:.78rem;color:var(--accent2)">${t.analyst ? '📡 '+escHtml(t.analyst) : '<span style="color:var(--muted)">—</span>'}</td>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:.78rem">
        ${t.setup_type ? '<span style="color:var(--accent2);margin-right:4px">'+escHtml(t.setup_type)+'</span>' : ''}${t.notes || (t.tags ? '🏷 ' + t.tags : '')}</td>
    </tr>`).join('');

  // Pagination
  const pg = document.getElementById('journal-pagination');
  if (pages <= 1) { pg.innerHTML = ''; return; }
  let html = '';
  if (page > 1) html += `<button class="page-btn" onclick="journalLoad(${page-1})">← Prev</button>`;
  const start = Math.max(1, page - 2);
  const end   = Math.min(pages, page + 2);
  for (let i = start; i <= end; i++) {
    html += `<button class="page-btn${i===page?' active':''}" onclick="journalLoad(${i})">${i}</button>`;
  }
  if (page < pages) html += `<button class="page-btn" onclick="journalLoad(${page+1})">Next →</button>`;
  pg.innerHTML = html;
}

function journalReset() {
  ['j-search','j-from','j-to'].forEach(id => document.getElementById(id).value = '');
  ['j-symbol','j-direction','j-pnl','j-setup'].forEach(id => document.getElementById(id).value = '');
  journalLoad(1);
}

function escHtml(s) { return (s||'').replace(/'/g,"&#39;").replace(/"/g,'&quot;'); }

// ── Add Trade Modal ────────────────────────────────────────────────────────────
function openAddModal() {
  document.getElementById('modal-title').textContent = 'Add Manual Trade';
  ['m-symbol','m-open','m-close','m-entry','m-exit','m-size','m-pnl','m-fees','m-notes','m-tags'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('m-setup').value = '';
  document.getElementById('trade-modal').classList.add('open');
}
function closeModal() { document.getElementById('trade-modal').classList.remove('open'); }

async function saveTrade() {
  const body = {
    symbol:       document.getElementById('m-symbol').value.trim().toUpperCase(),
    direction:    document.getElementById('m-direction').value,
    open_time:    document.getElementById('m-open').value.trim(),
    close_time:   document.getElementById('m-close').value.trim(),
    entry_price:  parseFloat(document.getElementById('m-entry').value) || 0,
    close_price:  parseFloat(document.getElementById('m-exit').value) || 0,
    size_usdt:    parseFloat(document.getElementById('m-size').value) || 0,
    realized_pnl: parseFloat(document.getElementById('m-pnl').value) || 0,
    total_fees:   parseFloat(document.getElementById('m-fees').value) || 0,
    margin_mode:  document.getElementById('m-margin').value,
    setup_type:   document.getElementById('m-setup').value,
    notes:        document.getElementById('m-notes').value,
    tags:         document.getElementById('m-tags').value,
  };
  if (!body.symbol || !body.open_time || !body.close_time) {
    alert('Symbol, open time and close time are required.');
    return;
  }
  const res = await api('/api/positions', 'POST', body);
  if (res.ok) { closeModal(); journalLoad(journalPage); }
  else alert('Error: ' + res.error);
}

// ── Edit Notes Modal ───────────────────────────────────────────────────────────
function openNotesModal(id, notes, tags, analyst, setupType, grade, gradeReason, callId) {
  document.getElementById('edit-id').value      = id;
  document.getElementById('edit-analyst').value = analyst || '';
  document.getElementById('edit-setup').value   = setupType || '';
  document.getElementById('edit-notes').value   = notes;
  document.getElementById('edit-tags').value    = tags;
  document.getElementById('edit-call-id').value = callId || '';

  const gradeBox = document.getElementById('edit-grade-box');
  if (grade) {
    document.getElementById('edit-grade-badge').textContent  = grade;
    document.getElementById('edit-grade-badge').className    = `grade-badge grade-${grade}`;
    document.getElementById('edit-grade-reason').textContent = gradeReason || '';
    gradeBox.style.display = 'block';
  } else {
    gradeBox.style.display = 'none';
  }
  document.getElementById('notes-modal').classList.add('open');
}
function closeNotesModal() { document.getElementById('notes-modal').classList.remove('open'); }

async function saveNotes() {
  const id     = document.getElementById('edit-id').value;
  const callId = document.getElementById('edit-call-id').value.trim();
  const body   = {
    analyst:    document.getElementById('edit-analyst').value.trim(),
    notes:      document.getElementById('edit-notes').value,
    tags:       document.getElementById('edit-tags').value,
    setup_type: document.getElementById('edit-setup').value,
    call_id:    callId ? parseInt(callId) : null,
  };
  const res = await api('/api/positions/' + id, 'PUT', body);
  if (res.ok) { closeNotesModal(); journalLoad(journalPage); }
  else alert('Error: ' + res.error);
}

async function gradePosition(id, btn) {
  const origText = btn.textContent;
  btn.textContent = '…';
  btn.disabled    = true;
  const res = await api('/api/positions/' + id + '/grade', 'POST');
  btn.disabled = false;
  if (res.ok) {
    btn.textContent = origText;
    // Update grade badge in the same cell
    const cell = btn.parentElement;
    let badge = cell.querySelector('.grade-badge');
    if (!badge) {
      badge = document.createElement('span');
      cell.insertBefore(badge, btn);
    }
    badge.className   = `grade-badge grade-${res.data.grade}`;
    badge.textContent = res.data.grade;
    badge.title       = res.data.reason || '';
  } else {
    btn.textContent = origText;
    alert('Grading failed: ' + (res.error || 'unknown error'));
  }
}

async function deleteTrade() {
  const id = document.getElementById('edit-id').value;
  if (!confirm('Delete this trade? This cannot be undone.')) return;
  const res = await api('/api/positions/' + id, 'DELETE');
  if (res.ok) { closeNotesModal(); journalLoad(journalPage); }
  else alert('Error: ' + res.error);
}

// ══════════════════════════════════════════════════════════════════════════════
// DEEP DIVE
// ══════════════════════════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════════════════════════
// HEATMAP
// ══════════════════════════════════════════════════════════════════════════════
function renderHeatmap(rows) {
  const container = document.getElementById('heatmap-container');
  if (!container) return;
  if (!rows.length) {
    container.innerHTML = '<div style="color:var(--muted);padding:8px">No trade data yet.</div>';
    return;
  }

  // Build lookup grid[weekday][hour]
  const grid = {};
  for (const r of rows) {
    if (!grid[r.weekday]) grid[r.weekday] = {};
    grid[r.weekday][r.hour] = r;
  }

  const days    = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const MIN     = 3;

  let html = '<table style="border-collapse:collapse;font-size:.7rem;width:100%">';
  html += '<tr><th style="padding:3px 8px;color:var(--muted);text-align:right;width:44px">UTC</th>';
  for (const d of days)
    html += `<th style="padding:3px 6px;color:var(--muted);text-align:center;min-width:52px">${d}</th>`;
  html += '</tr>';

  for (let h = 0; h < 24; h++) {
    html += `<tr><td style="padding:2px 8px;color:var(--muted);text-align:right;font-size:.68rem;white-space:nowrap">${String(h).padStart(2,'0')}:00</td>`;
    for (let d = 0; d < 7; d++) {
      const c = (grid[d] || {})[h];
      if (!c || c.trade_count < MIN) {
        html += '<td style="padding:2px;background:var(--bg3);border:1px solid var(--bg)"></td>';
      } else {
        const wr  = c.win_rate;
        const opc = Math.min(0.85, 0.25 + c.trade_count / 25);
        const bg  = wr >= 65 ? `rgba(38,217,107,${opc})`
                  : wr >= 50 ? `rgba(79,195,247,${opc})`
                  : wr >= 40 ? `rgba(255,179,0,${opc})`
                  :            `rgba(239,83,80,${opc})`;
        const pnl = c.total_pnl >= 0 ? `+${c.total_pnl.toFixed(0)}` : c.total_pnl.toFixed(0);
        html += `<td style="padding:3px 2px;background:${bg};border:1px solid var(--bg);text-align:center;cursor:default"
                    title="${c.trade_count} trades · ${wr}% WR · ${pnl} USDT">
                   <div style="font-weight:700">${wr}%</div>
                   <div style="opacity:.75">${c.trade_count}t</div>
                 </td>`;
      }
    }
    html += '</tr>';
  }
  html += '</table>';
  container.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════════════
// POSITION SIZING CALCULATOR
// ══════════════════════════════════════════════════════════════════════════════
let _szEquity = 0;

function calcSizing() {
  const entry  = parseFloat(document.getElementById('sz-entry')?.value) || 0;
  const sl     = parseFloat(document.getElementById('sz-sl')?.value)    || 0;
  const risk   = parseFloat(document.getElementById('sz-risk')?.value)  || 1;
  const equity = _szEquity || 0;
  const out    = document.getElementById('sz-result');
  if (!out) return;

  localStorage.setItem('sz_risk_pct', risk);

  if (!entry || !sl || !equity) {
    out.innerHTML = `<span style="color:var(--muted)">Waiting for entry, SL${!equity ? ' and account equity' : ''}…</span>`;
    return;
  }
  const riskDist = Math.abs(entry - sl) / entry;
  if (riskDist <= 0) { out.innerHTML = '<span style="color:var(--red)">SL must differ from entry</span>'; return; }

  const riskAmt  = equity * risk / 100;
  const sizeUsdt = riskAmt / riskDist;
  const leverage = sizeUsdt / equity;
  const levColor = leverage > 15 ? 'var(--red)' : leverage > 7 ? 'var(--yellow)' : 'var(--accent3)';

  out.innerHTML = `<div style="display:flex;gap:20px;flex-wrap:wrap;padding:8px 0">
    <div><div style="color:var(--muted);font-size:.7rem;text-transform:uppercase">Risk Amount</div>
         <div style="font-weight:700;color:var(--yellow)">${fmtC(riskAmt)} USDT (${risk}%)</div></div>
    <div><div style="color:var(--muted);font-size:.7rem;text-transform:uppercase">Position Size</div>
         <div style="font-weight:700">${fmtC(sizeUsdt)} USDT</div></div>
    <div><div style="color:var(--muted);font-size:.7rem;text-transform:uppercase">Leverage</div>
         <div style="font-weight:700;color:${levColor}">${leverage.toFixed(1)}x</div></div>
    <div><div style="color:var(--muted);font-size:.7rem;text-transform:uppercase">Risk Distance</div>
         <div style="font-weight:700;color:var(--muted)">${(riskDist * 100).toFixed(2)}%</div></div>
  </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// PATTERN DETECTOR
// ══════════════════════════════════════════════════════════════════════════════
async function runPatternDetector() {
  const btn = document.getElementById('pattern-btn');
  const box = document.getElementById('pattern-results');
  btn.disabled    = true;
  btn.textContent = '🔍 Analysing…';
  box.innerHTML   = '<div style="color:var(--muted);font-size:.85rem;padding:8px 0">Running Claude analysis on your trade history…</div>';

  const res = await api('/api/analytics/patterns', 'POST');
  btn.disabled    = false;
  btn.textContent = '🔍 Detect Patterns';

  if (!res.ok) {
    box.innerHTML = `<div style="color:var(--red)">Analysis failed: ${res.error || 'unknown error'}</div>`;
    return;
  }

  const d = res.data;
  if (d.insufficient_data) {
    box.innerHTML = `<div style="color:var(--muted);font-size:.85rem;padding:8px 0">${d.message}</div>`;
    return;
  }

  const typeStyle = {
    warning:  { icon: '⚠️', border: 'var(--red)',     bg: 'rgba(239,83,80,.08)',    label: 'Warning',  lc: 'var(--red)' },
    insight:  { icon: '💡', border: 'var(--yellow)',   bg: 'rgba(255,179,0,.08)',    label: 'Insight',  lc: 'var(--yellow)' },
    strength: { icon: '✅', border: 'var(--accent3)',  bg: 'rgba(38,217,107,.08)',   label: 'Strength', lc: 'var(--accent3)' },
  };
  const confColor = { high: 'var(--accent3)', medium: 'var(--yellow)', low: 'var(--muted)' };

  box.innerHTML = `
    <div style="font-size:.78rem;color:var(--muted);margin-bottom:12px">
      Based on ${d.trade_count} trades — ${d.findings.length} pattern${d.findings.length !== 1 ? 's' : ''} found
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px">
      ${d.findings.map(f => {
        const s = typeStyle[f.type] || typeStyle.insight;
        return `<div style="background:${s.bg};border:1px solid ${s.border};border-radius:var(--radius);padding:16px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <span style="font-size:1.1rem">${s.icon}</span>
            <span style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:${s.lc}">${s.label}</span>
            <span style="margin-left:auto;font-size:.68rem;color:${confColor[f.confidence] || 'var(--muted)'};text-transform:uppercase">${f.confidence} confidence</span>
          </div>
          <div style="font-weight:700;margin-bottom:6px;font-size:.9rem">${f.title}</div>
          <div style="font-size:.82rem;color:var(--muted);line-height:1.5;margin-bottom:8px">${f.finding}</div>
          <div style="font-size:.82rem;color:var(--text);border-top:1px solid ${s.border};padding-top:8px;margin-top:4px">
            → ${f.recommendation}
          </div>
        </div>`;
      }).join('')}
    </div>`;
}

async function loadDeep() {
  const res = await api('/api/analytics/deep');
  if (!res.ok) return;
  const d = res.data;

  // By Symbol bar chart (top 15)
  const sym = d.by_symbol.slice(0, 15);
  makeChart('bySymbolChart', 'bar', {
    labels: sym.map(s => s.symbol),
    datasets: [{
      label: 'Total P&L (USDT)',
      data: sym.map(s => s.total_pnl),
      backgroundColor: sym.map(s => s.total_pnl >= 0 ? 'rgba(38,217,107,.7)' : 'rgba(239,83,80,.7)'),
    }]
  }, { indexAxis: 'y', plugins: { legend: { display: false } } });

  // Monthly PnL
  makeChart('byMonthChart', 'bar', {
    labels: d.by_month.map(m => m.month),
    datasets: [{
      label: 'Monthly P&L (USDT)',
      data: d.by_month.map(m => m.total_pnl),
      backgroundColor: d.by_month.map(m => m.total_pnl >= 0 ? 'rgba(108,99,255,.7)' : 'rgba(239,83,80,.7)'),
    }]
  });

  // By weekday
  const wdays = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  makeChart('byWeekdayChart', 'bar', {
    labels: d.by_weekday.map(w => w.weekday),
    datasets: [{
      label: 'Total P&L (USDT)',
      data: d.by_weekday.map(w => w.total_pnl),
      backgroundColor: 'rgba(79,195,247,.7)',
    }]
  });

  // By hour (0-23)
  makeChart('byHourChart', 'bar', {
    labels: d.by_hour.map(h => h.hour + ':00'),
    datasets: [{
      label: 'Total P&L (USDT)',
      data: d.by_hour.map(h => h.total_pnl),
      backgroundColor: 'rgba(255,179,0,.7)',
    }]
  });

  // Long vs Short
  makeChart('byDirectionChart', 'doughnut', {
    labels: d.by_direction.map(x => x.direction + ' (' + x.trade_count + ')'),
    datasets: [{
      data: d.by_direction.map(x => x.trade_count),
      backgroundColor: ['rgba(38,217,107,.7)', 'rgba(239,83,80,.7)'],
      borderWidth: 0,
    }]
  }, { scales: undefined,
       plugins: { legend: { position: 'bottom', labels: { color: '#7986cb' } } } });

  // Duration buckets
  makeChart('durationChart', 'bar', {
    labels: d.duration_buckets.map(b => b.label),
    datasets: [{
      label: 'Trades',
      data: d.duration_buckets.map(b => b.count),
      backgroundColor: 'rgba(108,99,255,.7)',
    }]
  });

  // Stat pills
  const fa = d.fee_analysis;
  const str = d.streaks;
  document.getElementById('deep-stat-pills').innerHTML = [
    `<div class="stat-pill">Max Win Streak: <strong class="pos">${str.max_win_streak}</strong></div>`,
    `<div class="stat-pill">Max Loss Streak: <strong class="neg">${str.max_loss_streak}</strong></div>`,
    `<div class="stat-pill">Total Fees: <strong class="neg">${fmtC(fa.total_fees)} USDT</strong></div>`,
    `<div class="stat-pill">Avg Fee/Trade: <strong class="neg">${fmtC(fa.avg_fee)} USDT</strong></div>`,
    `<div class="stat-pill">Fees % Gross PnL: <strong class="neg">${fa.fee_pct_gross}%</strong></div>`,
  ].join('');

  // Symbol table
  document.getElementById('deep-symbol-tbody').innerHTML = d.by_symbol.map(s => `
    <tr>
      <td><strong>${s.symbol}</strong></td>
      <td>${s.trade_count}</td>
      <td class="${s.win_rate>=50?'pos':'neg'}">${s.win_rate}%</td>
      <td class="${pnlClass(s.total_pnl)}">${pnlSign(s.total_pnl)}${fmtC(s.total_pnl)}</td>
      <td class="${pnlClass(s.avg_pnl)}">${pnlSign(s.avg_pnl)}${fmtC(s.avg_pnl)}</td>
      <td class="pos">+${fmtC(s.best)}</td>
      <td class="neg">${fmtC(s.worst)}</td>
      <td class="neg">${fmtC(s.total_fees)}</td>
    </tr>`).join('');

  // Worst symbols
  document.getElementById('deep-worst-tbody').innerHTML = d.worst_symbols.map(s => `
    <tr>
      <td><strong>${s.symbol}</strong></td>
      <td>${s.trade_count}</td>
      <td class="neg">${fmtC(s.total_pnl)}</td>
    </tr>`).join('');

  // Heatmap
  const hmRes = await api('/api/analytics/heatmap');
  if (hmRes.ok) renderHeatmap(hmRes.data);

}

// ══════════════════════════════════════════════════════════════════════════════
// EDGE LAB
// ══════════════════════════════════════════════════════════════════════════════
async function loadEdge() {
  const res = await api('/api/analytics/deep');
  if (!res.ok) return;
  const d = res.data;

  // By setup type
  if (d.by_setup && d.by_setup.length) {
    makeChart('bySetupChart', 'bar', {
      labels: d.by_setup.map(s => s.setup_type),
      datasets: [{
        label: 'Total P&L (USDT)',
        data: d.by_setup.map(s => s.total_pnl),
        backgroundColor: d.by_setup.map(s => s.total_pnl >= 0 ? 'rgba(108,99,255,.7)' : 'rgba(239,83,80,.7)'),
      }]
    }, { indexAxis: 'y', plugins: { legend: { display: false } } });

    makeChart('bySetupWinChart', 'bar', {
      labels: d.by_setup.map(s => s.setup_type),
      datasets: [{
        label: 'Win Rate (%)',
        data: d.by_setup.map(s => s.win_rate),
        backgroundColor: d.by_setup.map(s => s.win_rate >= 50 ? 'rgba(38,217,107,.7)' : 'rgba(239,83,80,.7)'),
      }]
    }, { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { max: 100 } } });

    document.getElementById('deep-setup-tbody').innerHTML = d.by_setup.map(s => `
      <tr>
        <td><strong>${s.setup_type}</strong></td>
        <td>${s.trade_count}</td>
        <td class="${s.win_rate>=50?'pos':'neg'}">${s.win_rate}%</td>
        <td class="${pnlClass(s.total_pnl)}">${pnlSign(s.total_pnl)}${fmtC(s.total_pnl)}</td>
        <td class="${pnlClass(s.avg_pnl)}">${pnlSign(s.avg_pnl)}${fmtC(s.avg_pnl)}</td>
      </tr>`).join('');
  }

  // By execution grade
  if (d.by_grade && d.by_grade.length) {
    document.getElementById('deep-grade-tbody').innerHTML = d.by_grade.map(g => `
      <tr>
        <td><span class="grade-badge grade-${g.grade}">${g.grade}</span></td>
        <td>${g.trade_count}</td>
        <td class="${g.win_rate>=50?'pos':'neg'}">${g.win_rate}%</td>
        <td class="${pnlClass(g.total_pnl)}">${pnlSign(g.total_pnl)}${fmtC(g.total_pnl)}</td>
        <td class="${pnlClass(g.avg_pnl)}">${pnlSign(g.avg_pnl)}${fmtC(g.avg_pnl)}</td>
      </tr>`).join('');
  }

  // Planned vs realized R:R
  const rrRes = await api('/api/analytics/rr');
  if (rrRes.ok && rrRes.data.items.length) {
    document.getElementById('deep-rr-tbody').innerHTML = rrRes.data.items.map(r => {
      const rrColor = r.realized_rr == null ? '' : r.realized_rr >= 1 ? 'pos' : 'neg';
      const rrText  = r.realized_rr != null ? `<span class="${rrColor}">${r.realized_rr}R</span>` : '—';
      return `<tr>
        <td><strong>${r.symbol}</strong></td>
        <td><span class="badge ${(r.direction||'').toLowerCase()}">${r.direction}</span></td>
        <td style="color:var(--accent2);font-size:.78rem">${r.setup_type||'—'}</td>
        <td>${r.grade ? `<span class="grade-badge grade-${r.grade}">${r.grade}</span>` : '—'}</td>
        <td style="color:var(--muted)">${r.planned_rr||'—'}</td>
        <td>${rrText}</td>
        <td style="font-size:.78rem;color:var(--muted)">${r.outcome||'—'}</td>
        <td class="${pnlClass(r.pnl)}">${pnlSign(r.pnl)}${fmtC(r.pnl)}</td>
      </tr>`;
    }).join('');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// AI ADVISOR
// ══════════════════════════════════════════════════════════════════════════════
async function runAI() {
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('ai-loading').style.display = 'block';
  document.getElementById('ai-results').style.display = 'none';

  try {
    const res = await api('/api/ai/analyze', 'POST', {});
    if (!res.ok) throw new Error(res.error);
    const d = res.data;
    renderAI(d);
    document.getElementById('ai-last-run').textContent =
      `Last analyzed: ${new Date().toLocaleString()} · ${d._input_tokens} in / ${d._output_tokens} out tokens`;
  } catch(e) {
    document.getElementById('ai-results').innerHTML =
      `<div class="upload-result error">Error: ${e.message}</div>`;
    document.getElementById('ai-results').style.display = 'block';
  } finally {
    document.getElementById('ai-loading').style.display = 'none';
    document.getElementById('btn-analyze').disabled = false;
  }
}

function renderAI(d) {
  const scoreColor = d.score?.value >= 7 ? 'var(--accent3)' :
                     d.score?.value >= 5 ? 'var(--yellow)' : 'var(--red)';
  let html = `
    <div class="ai-score">
      <div class="ai-score-num" style="color:${scoreColor}">${d.score?.value ?? '—'}/10</div>
      <div class="ai-score-label">${d.score?.label ?? ''} Trader</div>
    </div>
    <div class="ai-overall">${d.overall_status || ''}</div>`;

  if (d.strengths?.length) {
    html += `<div class="ai-section"><h3>💪 Strengths</h3>` +
      d.strengths.map(s => `
        <div class="ai-item">
          <div class="ai-item-title">${s.title}</div>
          <div class="ai-item-detail">${s.detail}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.weaknesses?.length) {
    html += `<div class="ai-section"><h3>⚠️ Areas to Improve</h3>` +
      d.weaknesses.map(w => `
        <div class="ai-item">
          <div class="ai-item-title">${w.title}</div>
          <div class="ai-item-detail">${w.detail}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.recommendations?.length) {
    html += `<div class="ai-section"><h3>🎯 Action Plan</h3>` +
      d.recommendations.map(r => `
        <div class="ai-item">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <span class="priority-${r.priority?.toLowerCase()}">${r.priority}</span>
            <span class="ai-item-title">${r.title}</span>
          </div>
          <div class="ai-item-detail"><strong>Action:</strong> ${r.action}</div>
          <div class="ai-item-detail"><strong>Expected:</strong> ${r.expected_impact}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.symbol_insights?.length) {
    html += `<div class="ai-section"><h3>📊 Symbol Insights</h3>` +
      d.symbol_insights.map(s => `
        <div class="ai-item">
          <div class="ai-item-title">${s.symbol}</div>
          <div class="ai-item-detail">${s.insight}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.risk_management) {
    html += `<div class="ai-section"><h3>🛡 Risk Management</h3>
      <div class="ai-item-detail" style="line-height:1.7">${d.risk_management}</div></div>`;
  }

  if (d.mindset_note) {
    html += `<div class="ai-section" style="border-color:var(--accent);background:rgba(108,99,255,.05)">
      <h3>🧠 Mindset</h3>
      <div class="ai-item-detail" style="line-height:1.7">${d.mindset_note}</div></div>`;
  }

  document.getElementById('ai-results').innerHTML = html;
  document.getElementById('ai-results').style.display = 'block';
}

// ══════════════════════════════════════════════════════════════════════════════
// IMPORT
// ══════════════════════════════════════════════════════════════════════════════
function handleDrop(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.remove('dragover');
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
}

async function handleFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  document.getElementById('upload-result').innerHTML =
    '<div class="upload-result" style="background:var(--bg2);border:1px solid var(--border)">Uploading…</div>';
  try {
    const r = await fetch('/api/import', { method: 'POST', body: fd });
    const res = await r.json();
    if (res.ok) {
      const d = res.data;
      document.getElementById('upload-result').innerHTML =
        `<div class="upload-result success">
          ✅ Import complete!
          Positions: ${d.positions??0} · Orders: ${d.orders??0} · Transactions: ${d.transactions??0}
        </div>`;
      loadImportLog();
    } else {
      document.getElementById('upload-result').innerHTML =
        `<div class="upload-result error">❌ ${res.error}</div>`;
    }
  } catch(e) {
    document.getElementById('upload-result').innerHTML =
      `<div class="upload-result error">❌ ${e.message}</div>`;
  }
}

async function loadImportLog() {
  const res = await api('/api/import/status');
  if (!res.ok) return;
  document.getElementById('import-log-tbody').innerHTML = res.data.length === 0
    ? '<tr><td colspan="4" style="text-align:center;color:var(--muted)">No imports yet</td></tr>'
    : res.data.map(r => `
        <tr>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${r.filename}</td>
          <td>${r.file_type}</td>
          <td>${r.rows_imported}</td>
          <td>${r.imported_at?.slice(0,16)}</td>
        </tr>`).join('');
}

// ── Close modals on overlay click ──────────────────────────────────────────────
document.getElementById('trade-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
document.getElementById('notes-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeNotesModal();
});

// ══════════════════════════════════════════════════════════════════════════════
// CALL ANALYZER
// ══════════════════════════════════════════════════════════════════════════════
let callImageB64  = null;
let callImageType = 'image/png';

let _deepStatsCache = null;

async function loadCallEquity() {
  try {
    const res = await api('/api/sync/status');
    const eq  = parseFloat(res.data?.account_equity || 0);
    if (eq) {
      document.getElementById('call-equity-label').textContent =
        `Account equity: ${eq.toFixed(2)} USDT (used for sizing)`;
      _szEquity = eq;
      // Restore saved risk %
      const saved = localStorage.getItem('sz_risk_pct');
      if (saved) document.getElementById('sz-risk').value = saved;
      calcSizing();
    }
  } catch(e) {}

  // Fetch deep stats for time-of-day warning (cached)
  if (!_deepStatsCache) {
    try {
      const dr = await api('/api/analytics/deep');
      if (dr.ok) _deepStatsCache = dr.data;
    } catch(e) {}
  }
  if (_deepStatsCache) showCallTimeWarning(_deepStatsCache);
}

function showCallTimeWarning(deep) {
  const el = document.getElementById('call-time-warning');
  if (!el) return;
  const nowHour = new Date().getUTCHours();
  const nowDayIdx = new Date().getUTCDay(); // 0=Sun
  const dayNames  = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const nowDay    = dayNames[nowDayIdx];

  const worstHours = [...(deep.by_hour || [])].sort((a,b) => a.total_pnl - b.total_pnl).slice(0,3);
  const worstDays  = [...(deep.by_weekday || [])].sort((a,b) => a.total_pnl - b.total_pnl).slice(0,2);

  const warnings = [];
  const hourHit  = worstHours.find(h => h.hour === nowHour);
  const dayHit   = worstDays.find(d  => d.weekday === nowDay);

  if (hourHit) warnings.push(`${nowHour}:00 UTC is your #${worstHours.indexOf(hourHit)+1} worst hour (${fmtC(hourHit.total_pnl)} USDT, ${hourHit.win_rate}% WR)`);
  if (dayHit)  warnings.push(`${nowDay} is one of your worst trading days (${fmtC(dayHit.total_pnl)} USDT, ${dayHit.win_rate}% WR)`);

  if (warnings.length) {
    el.innerHTML = `⚠ <strong>Poor timing detected:</strong> ${warnings.join(' · ')} — consider waiting for a better window`;
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}

function handleCallImageDrop(e) {
  e.preventDefault();
  document.getElementById('call-img-drop').classList.remove('dragover');
  const f = e.dataTransfer.files[0];
  if (f) handleCallImageFile(f);
}

function handleCallImageFile(file) {
  if (!file) return;
  callImageType = file.type || 'image/png';
  const reader  = new FileReader();
  reader.onload = ev => {
    const result = ev.target.result;
    // Strip the data:image/xxx;base64, prefix
    callImageB64  = result.split(',')[1];
    const preview = document.getElementById('call-img-preview');
    preview.src   = result;
    preview.style.display = 'block';
    document.getElementById('call-img-drop').querySelector('p').style.display = 'none';
  };
  reader.readAsDataURL(file);
}

function clearCall() {
  document.getElementById('call-text').value = '';
  callImageB64  = null;
  const preview = document.getElementById('call-img-preview');
  preview.style.display = 'none';
  preview.src   = '';
  document.getElementById('call-img-drop').querySelector('p').style.display = 'block';
  document.getElementById('call-img-input').value = '';
  document.getElementById('call-result').classList.add('hidden');
  document.getElementById('call-result').innerHTML = '';
}

async function analyzeCall() {
  const text = document.getElementById('call-text').value.trim();
  if (!text) { alert('Please paste a trade call first.'); return; }

  const btn    = document.getElementById('call-analyze-btn');
  const result = document.getElementById('call-result');
  const loading= document.getElementById('call-loading');

  btn.disabled = true;
  result.classList.add('hidden');
  loading.style.display = 'block';

  try {
    const regime = document.getElementById('call-regime')?.value || '';
    const body = { call_text: text, market_regime: regime || null };
    if (callImageB64) { body.image_b64 = callImageB64; body.image_type = callImageType; }

    const res = await api('/api/calls/analyze', 'POST', body);
    loading.style.display = 'none';

    if (!res.ok) { result.innerHTML = `<div class="upload-result error">❌ ${res.error}</div>`; }
    else          { renderCallResult(res.data); }
    result.classList.remove('hidden');
  } catch(e) {
    loading.style.display = 'none';
    result.innerHTML = `<div class="upload-result error">❌ ${e.message}</div>`;
    result.classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

function renderCallResult(d) {
  // Auto-fill sizing calculator with parsed entry and SL
  const bs = d.bitget_settings || {};
  const entryP = parseFloat(bs.entry_price || d.entry_price || 0);
  const slP    = parseFloat(bs.sl_price    || d.sl_price    || 0);
  if (entryP && document.getElementById('sz-entry')) {
    document.getElementById('sz-entry').value = entryP;
  }
  if (slP && document.getElementById('sz-sl')) {
    document.getElementById('sz-sl').value = slP;
  }
  if (entryP || slP) calcSizing();

  const result  = document.getElementById('call-result');
  const sq      = d.setup_quality || {};
  const rr      = d.risk_reward   || {};
  const bs      = d.bitget_settings || {};
  const sz      = d._sizing       || {};
  const hist    = d._history      || {};
  const qlabel  = (sq.label || 'Unknown').toLowerCase();

  let html = `
    <!-- Header -->
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px">
      <div>
        <div style="font-size:1.3rem;font-weight:800">${d.symbol || ''} ${d.direction || ''}</div>
        <div style="font-size:.8rem;color:var(--muted)">${d.trade_type || ''} ${d.has_dca ? '· DCA' : '· No DCA'}</div>
      </div>
      <span class="quality-badge quality-${qlabel}">${sq.score || 0}/10 ${sq.label || ''}</span>
      ${rr.ratio ? `<span class="rr-pill">R:R ${rr.ratio}</span>` : ''}
    </div>`;

  // Drawdown warning if sizing was reduced
  if (sz.drawdown_note) {
    html += `<div class="warn-box" style="margin-bottom:14px">
      <strong>⚠ Drawdown Protection Active</strong> ${sz.drawdown_note}
      — base risk ${sz.base_risk_pct}% reduced to ${sz.risk_pct}%
    </div>`;
  }

  // Pattern flags from Claude
  if (d.pattern_flags?.length) {
    html += `<div class="pattern-flag-box">
      <div class="pf-title">⚠ Personal Pattern Warnings</div>
      ${d.pattern_flags.map(f => `<div style="margin-bottom:4px;color:var(--text)">• ${f}</div>`).join('')}
    </div>`;
  }

  // Chart analysis (if available)
  if (d.chart_analysis) {
    html += `<div class="ai-overall" style="margin-bottom:16px">${d.chart_analysis}</div>`;
  }

  // Position sizing grid
  if (sz.total_notional_usdt) {
    html += `
    <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--muted);letter-spacing:.05em;margin-bottom:8px">
      Position Sizing — ${sz.risk_pct}% Risk = ${sz.risk_amount_usdt} USDT at risk
    </div>
    <div class="sizing-grid">
      <div class="sizing-cell">
        <div class="sizing-cell-label">Total Notional</div>
        <div class="sizing-cell-val size">${sz.total_notional_usdt} USDT</div>
      </div>
      <div class="sizing-cell">
        <div class="sizing-cell-label">Margin Needed</div>
        <div class="sizing-cell-val">${sz.margin_needed_usdt} USDT</div>
      </div>
      <div class="sizing-cell">
        <div class="sizing-cell-label">Leverage</div>
        <div class="sizing-cell-val">${sz.leverage}x</div>
      </div>
      <div class="sizing-cell">
        <div class="sizing-cell-label">Stop Distance</div>
        <div class="sizing-cell-val neg">${sz.stop_dist_pct}%</div>
      </div>
      ${sz.entry_1_notional ? `
      <div class="sizing-cell">
        <div class="sizing-cell-label">Entry 1 (${sz.entry_1_pct}%)</div>
        <div class="sizing-cell-val entry">${sz.entry_1_notional} USDT</div>
      </div>` : ''}
      ${sz.entry_2_notional ? `
      <div class="sizing-cell">
        <div class="sizing-cell-label">DCA Entry 2 (${sz.entry_2_pct}%)</div>
        <div class="sizing-cell-val entry">${sz.entry_2_notional} USDT</div>
      </div>` : ''}
    </div>`;
  }

  // Position Size Checker (after sizing grid)
  if (sz.total_notional_usdt) {
    html += `
    <div class="risk-checker" id="size-checker-panel"
         data-recommended="${sz.total_notional_usdt}" data-risk="${sz.risk_pct || 1}">
      <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:8px">
        ✓ Position Size Checker
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.82rem;color:var(--muted)">What notional did you actually open?</span>
        <input type="number" id="actual-notional-input" placeholder="${sz.total_notional_usdt}"
               style="width:120px;background:var(--bg);border:1px solid var(--border);border-radius:6px;
                      color:var(--text);padding:6px 10px;font-size:.82rem"
               step="any" oninput="checkPositionSize(this.value)">
        <span style="font-size:.82rem;color:var(--muted)">USDT</span>
      </div>
      <div id="size-check-result" style="font-weight:700;margin-top:6px;font-size:.82rem"></div>
    </div>`;
  }

  // Bitget settings box
  html += `<div class="bitget-box"><h4>⚙ Bitget Entry Settings</h4>`;
  const bRows = [
    ['Symbol',       bs.symbol,     ''],
    ['Direction',    bs.direction,  'entry'],
    ['Margin Mode',  bs.margin_mode,''],
    ['Leverage',     bs.leverage,   'entry'],
  ];
  bRows.forEach(([k,v,cls]) => {
    if (v) html += `<div class="bitget-row"><span class="bitget-key">${k}</span><span class="bitget-val ${cls}">${v}</span></div>`;
  });

  if (bs.order_1) {
    const o1 = bs.order_1;
    html += `<div class="bitget-row">
      <span class="bitget-key">Order 1</span>
      <span class="bitget-val entry">${o1.type} · ${o1.notional_usdt ? o1.notional_usdt + ' USDT' : ''} ${o1.note ? '— ' + o1.note : ''}</span>
    </div>`;
  }
  if (bs.order_2) {
    const o2 = bs.order_2;
    html += `<div class="bitget-row">
      <span class="bitget-key">DCA Order 2</span>
      <span class="bitget-val entry">Limit @ ${o2.price} · ${o2.notional_usdt ? o2.notional_usdt + ' USDT' : ''}</span>
    </div>`;
  }
  if (bs.stop_loss) {
    const sl = bs.stop_loss;
    html += `<div class="bitget-row">
      <span class="bitget-key">Stop Loss</span>
      <span class="bitget-val sl">${sl.price} (${sl.type})</span>
    </div>`;
    if (sl.bitget_instruction) html += `<div class="bitget-row">
      <span class="bitget-key" style="color:var(--red)">SL Instruction</span>
      <span class="bitget-val" style="color:var(--yellow);font-weight:400;font-size:.78rem">${sl.bitget_instruction}</span>
    </div>`;
  }
  if (bs.take_profit_1) {
    html += `<div class="bitget-row">
      <span class="bitget-key">Take Profit 1</span>
      <span class="bitget-val tp">${bs.take_profit_1.price} ${bs.take_profit_1.note ? '— ' + bs.take_profit_1.note : ''}</span>
    </div>`;
  }
  if (bs.take_profit_2) {
    html += `<div class="bitget-row">
      <span class="bitget-key">Take Profit 2</span>
      <span class="bitget-val tp">${bs.take_profit_2.price} ${bs.take_profit_2.note ? '— ' + bs.take_profit_2.note : ''}</span>
    </div>`;
  }
  html += `</div>`;

  // Candle-close SL warning
  if (d.has_candle_close_sl && d.sl_warning) {
    html += `<div class="warn-box"><strong>⚠ Candle-Close Stop Loss</strong>${d.sl_warning}</div>`;
  }

  // Entry timing
  if (d.entry_timing) {
    html += `<div style="margin:12px 0;font-size:.83rem">
      <span style="color:var(--muted);text-transform:uppercase;font-size:.72rem;font-weight:700">Entry Timing</span><br>
      <span style="margin-top:4px;display:block">${d.entry_timing}</span>
    </div>`;
  }

  // Summary
  if (d.summary) {
    html += `<div class="ai-overall" style="margin:14px 0">${d.summary}</div>`;
  }

  // Optimizations
  if (d.optimizations?.length) {
    html += `<div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--muted);margin:14px 0 6px">Optimizations</div>
    <ul class="call-list">${d.optimizations.map(o => `<li><span>💡</span>${o}</li>`).join('')}</ul>`;
  }

  // Risks
  if (d.risks?.length) {
    html += `<div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--muted);margin:14px 0 6px">Risks</div>
    <ul class="call-list">${d.risks.map(r => `<li><span>⚠</span>${r}</li>`).join('')}</ul>`;
  }

  // Historical context
  if (d.historical_context) {
    html += `<div class="call-history-note">📊 ${d.historical_context}</div>`;
  }

  // Token usage
  html += `<div style="font-size:.7rem;color:var(--border);text-align:right;margin-top:12px">
    ${d._input_tokens} in / ${d._output_tokens} out tokens · claude-sonnet-4-6</div>`;

  result.innerHTML = html;

  // Append Save button after result renders
  result.insertAdjacentHTML('beforeend', `
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="call-save-btn" onclick="saveCurrentCall()">💾 Save This Call</button>
      <span id="call-save-msg" style="font-size:.8rem;color:var(--muted)"></span>
    </div>`);
}

// Store last analysis result for saving
let _lastCallResult = null;
const _origRenderCallResult = renderCallResult;
renderCallResult = function(d) { _lastCallResult = d; _origRenderCallResult(d); };

async function saveCurrentCall() {
  if (!_lastCallResult) return;
  const btn = document.getElementById('call-save-btn');
  const msg = document.getElementById('call-save-msg');
  btn.disabled = true;
  btn.textContent = '⏳ Saving…';
  try {
    const analyst = (document.getElementById('call-analyst')?.value || '').trim();
    const payload = Object.assign({}, _lastCallResult, { _analyst: analyst });
    const res = await api('/api/calls/save', 'POST', payload);
    if (res.ok) {
      btn.textContent = '✅ Saved';
      msg.textContent = `Saved as call #${res.data.id} — visible in Saved Calls below`;
      loadSavedCalls();
    } else {
      btn.textContent = '💾 Save This Call';
      btn.disabled = false;
      msg.textContent = '❌ ' + res.error;
    }
  } catch(e) {
    btn.textContent = '💾 Save This Call';
    btn.disabled = false;
    msg.textContent = '❌ ' + e.message;
  }
}

async function loadSavedCalls() {
  const res = await api('/api/calls/saved');
  if (!res.ok) return;
  const calls = res.data;
  const section = document.getElementById('saved-calls-section');
  const list    = document.getElementById('saved-calls-list');
  if (!calls.length) { section.style.display = 'none'; return; }
  section.style.display = 'block';

  list.innerHTML = calls.map(c => {
    const chipCls = { saved:'chip-saved', matched:'chip-matched',
                      dismissed:'chip-dismissed', closed:'chip-closed' }[c.status] || 'chip-saved';
    const outcomeTag = c.outcome ? `<span class="call-status-chip ${c.outcome==='won'?'chip-matched':c.outcome==='lost'?'chip-dismissed':'chip-saved'}">${c.outcome==='won'?'✅ Won':c.outcome==='lost'?'❌ Lost':'↩ Manual'} ${c.outcome_pnl!=null?fmtC(c.outcome_pnl)+' USDT':''}</span>` : '';
    return `
    <div class="saved-call-row ${c.status}" id="saved-row-${c.id}">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <strong>${c.symbol} ${c.direction}</strong>
          <span class="call-status-chip ${chipCls}">${c.status}</span>
          ${outcomeTag}
          ${c.analyst ? `<span style="font-size:.72rem;background:rgba(79,195,247,.1);color:var(--accent2);padding:1px 8px;border-radius:20px">📡 ${escHtml(c.analyst)}</span>` : ''}
          ${c.setup_score ? `<span style="font-size:.75rem;color:var(--muted)">${c.setup_score}/10 ${c.setup_label||''}</span>` : ''}
          ${c.rr_ratio ? `<span class="rr-pill" style="font-size:.72rem">${c.rr_ratio}</span>` : ''}
        </div>
        <div style="font-size:.75rem;color:var(--muted);margin-top:3px">
          ${c.trade_type||''} ·
          SL: <span style="color:var(--red)">${c.sl_price||'—'}</span> ·
          TP1: <span style="color:var(--accent3)">${c.tp1_price||'—'}</span>
          ${c.hit_tp1 ? '<span style="color:var(--accent3)">✓</span>' : ''} ·
          TP2: <span style="color:var(--accent3)">${c.tp2_price||'—'}</span>
          ${c.hit_tp2 ? '<span style="color:var(--accent3)">✓</span>' : ''} ·
          ${c.total_notional ? c.total_notional + ' USDT notional' : ''} ·
          Saved: ${(c.created_at||'').slice(0,16)}
          ${c.matched_at ? ' · Matched: ' + c.matched_at.slice(0,16) : ''}
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        ${c.status !== 'closed' ? `<button class="btn btn-sm" style="background:rgba(79,195,247,.12);color:var(--accent2);border:1px solid rgba(79,195,247,.3)" onclick="openLimitModal(null,{call_id:${c.id},symbol:'${c.symbol}',direction:'${c.direction}',sl_price:${c.sl_price||'null'},tp1_price:${c.tp1_price||'null'},tp2_price:${c.tp2_price||'null'},entry_price:${c.entry_price||'null'},total_notional:${c.total_notional||'null'},analyst:'${(c.analyst||'').replace(/\\/g,"\\\\").replace(/'/g,"\\'")}',leverage:${c.leverage||10}})">⏳ Set Limit</button>` : ''}
        ${(c.status === 'matched' || c.status === 'closed') && !c.outcome ? `<button class="btn btn-secondary btn-sm" onclick="openOutcomeModal(${c.id})">📊 Record Outcome</button>` : ''}
        ${c.status === 'matched' ? `<button class="btn btn-secondary btn-sm" onclick="closeCall(${c.id})">Mark Closed</button>` : ''}
        <button class="btn btn-danger btn-sm" onclick="deleteCall(${c.id})">Delete</button>
      </div>
    </div>`;
  }).join('');
}

async function deleteCall(id) {
  if (!confirm('Delete this saved call?')) return;
  await api('/api/calls/' + id, 'DELETE');
  loadSavedCalls();
}
async function closeCall(id) {
  await api('/api/calls/' + id + '/close', 'POST');
  loadSavedCalls();
}

// ══════════════════════════════════════════════════════════════════════════════
// LIVE TRADES
// ══════════════════════════════════════════════════════════════════════════════
let liveTradesInterval  = null;
let livePositionsCache  = [];
let liveAnalysisCache   = {};   // key: "SYMBOL_direction" → analysis result dict
let liveOpenPanels      = new Set();  // indices of cards with open AI panels
let liveCallMatches     = {};   // key: "SYMBOL_direction" → saved call data
let liveMarketCtx       = {};   // key: symbol → {funding, long_short}

async function loadLiveTrades() {
  document.getElementById('trades-refresh-label').textContent = 'Refreshing…';
  try {
    // Fetch positions and match checks in parallel
    const [posRes, matchRes] = await Promise.all([
      api('/api/live/positions'),
      api('/api/calls/check-matches'),
    ]);
    if (!posRes.ok) throw new Error(posRes.error);

    livePositionsCache = posRes.data.positions || [];
    const eq = posRes.data.equity || {};

    // Economic calendar warning (non-blocking)
    api('/api/market/calendar').then(cr => {
      const el = document.getElementById('eco-warning');
      if (!el) return;
      if (cr.ok && cr.data.length) {
        const lines = cr.data.map(e =>
          `📅 <strong>${e.title}</strong> ${e.when} ${e.time ? 'at ' + e.time + ' ET' : ''}` +
          `${e.forecast ? ' — Forecast: ' + e.forecast : ''}${e.previous ? ' · Prev: ' + e.previous : ''}`
        );
        el.innerHTML = '⚠ High-impact USD events:<br>' + lines.join('<br>');
        el.style.display = '';
      } else {
        el.style.display = 'none';
      }
    });

    // Fetch market context for all open symbols (non-blocking)
    if (livePositionsCache.length) {
      const syms = [...new Set(livePositionsCache.map(p => p.symbol))].join(',');
      api('/api/market/context?symbols=' + syms).then(mr => {
        if (mr.ok) {
          liveMarketCtx = mr.data.symbols || {};
          renderPositionCards(livePositionsCache);  // re-render with context
        }
      });
    }

    // Build match map: key = "SYMBOL_direction" → call
    // Only store 'saved' status matches (need confirmation)
    const pendingMatches = {};
    const confirmedMatches = {};
    if (matchRes.ok) {
      (matchRes.data || []).forEach(m => {
        const key = m.call.symbol + '_' + m.call.direction;
        if (m.call.status === 'matched') confirmedMatches[key] = m.call;
        else                             pendingMatches[key]   = m.call;
      });
    }
    // Also fetch already-confirmed matches (status=matched)
    const savedRes = await api('/api/calls/saved');
    if (savedRes.ok) {
      savedRes.data.filter(c => c.status === 'matched').forEach(c => {
        confirmedMatches[c.symbol + '_' + c.direction] = c;
      });
    }

    liveCallMatches = { ...confirmedMatches };

    renderLiveKpis(livePositionsCache, eq);
    renderMatchBanners(pendingMatches, livePositionsCache);
    renderCorrelationWarning(livePositionsCache);
    renderPositionCards(livePositionsCache);
    document.getElementById('trades-refresh-label').textContent =
      'Live · ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('trades-container').innerHTML =
      `<div class="upload-result error">❌ ${e.message}</div>`;
    document.getElementById('trades-refresh-label').textContent = 'Error';
  }
}

function renderMatchBanners(pendingMatches, positions) {
  const container = document.getElementById('match-confirmations');
  const entries   = Object.entries(pendingMatches);
  if (!entries.length) { container.innerHTML = ''; return; }

  container.innerHTML = entries.map(([key, call]) => {
    const pos = positions.find(p => p.symbol + '_' + p.direction === key);
    if (!pos) return '';
    const pnlStr = pos.unrealized_pnl >= 0 ? `+${fmtC(pos.unrealized_pnl)}` : fmtC(pos.unrealized_pnl);
    return `
    <div class="warn-box" style="margin-bottom:16px;display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap" id="match-banner-${call.id}">
      <div style="flex:1;min-width:200px">
        <strong style="font-size:.9rem;color:var(--text)">📡 Call Match Detected: ${call.symbol} ${call.direction}</strong>
        <div style="margin-top:4px;font-size:.8rem;line-height:1.5">
          You have an open ${pos.direction} on <strong>${pos.symbol}</strong> (${pnlStr} USDT unrealized)
          that matches your saved call from ${(call.created_at||'').slice(0,10)}.
          <br>Setup: ${call.setup_score||'?'}/10 ${call.setup_label||''} · ${call.trade_type||''} ·
          SL: <span style="color:var(--red)">${call.sl_price||'—'}</span> ·
          TP1: <span style="color:var(--accent3)">${call.tp1_price||'—'}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
        <button class="btn btn-primary btn-sm" onclick="confirmMatch(${call.id}, '${key}')">✅ Yes, this is that trade</button>
        <button class="btn btn-secondary btn-sm" onclick="dismissMatch(${call.id})">✗ Not this trade</button>
      </div>
    </div>`;
  }).join('');
}

async function confirmMatch(callId, key) {
  await api('/api/calls/' + callId + '/confirm-match', 'POST');
  document.getElementById('match-banner-' + callId)?.remove();
  // Fetch the call details and add to confirmed matches
  const savedRes = await api('/api/calls/saved');
  if (savedRes.ok) {
    const call = savedRes.data.find(c => c.id === callId);
    if (call) liveCallMatches[key] = call;
  }
  renderPositionCards(livePositionsCache);  // re-render with targets panel
}

async function dismissMatch(callId) {
  await api('/api/calls/' + callId + '/dismiss', 'POST');
  document.getElementById('match-banner-' + callId)?.remove();
}

function renderLiveKpis(positions, eq) {
  const totalUnrl  = positions.reduce((s, p) => s + p.unrealized_pnl, 0);
  const totalMargin= positions.reduce((s, p) => s + p.margin_usdt, 0);
  const equity     = parseFloat(eq.accountEquity || 0);
  const available  = parseFloat(eq.available || 0);
  const critical   = positions.filter(p => p.unrealized_pct < -30).length;

  document.getElementById('trades-kpi-grid').innerHTML = [
    { label: 'Open Positions', value: positions.length, cls: 'neu', sub: `${critical} critical` },
    { label: 'Total Unrealized P&L', value: (totalUnrl>=0?'+':'')+fmtC(totalUnrl)+' USDT',
      cls: pnlClass(totalUnrl), sub: 'Across all open trades' },
    { label: 'Margin In Use', value: fmtC(totalMargin)+' USDT', cls: 'neu', sub: 'Total collateral locked' },
    { label: 'Account Equity', value: fmtC(equity)+' USDT', cls: 'neu', sub: available.toFixed(2)+' available' },
  ].map(k => `<div class="kpi-card">
    <div class="kpi-label">${k.label}</div>
    <div class="kpi-value ${k.cls}">${k.value}</div>
    <div class="kpi-sub">${k.sub||''}</div>
  </div>`).join('');
}

function renderCallTargetsPanel(call, pos) {
  const mark   = parseFloat(pos.mark_price || 0);
  const dir    = pos.direction === 'Long' ? 1 : -1;
  const tp1p   = parseFloat(call.tp1_price || 0);
  const beP    = parseFloat(pos.break_even_price || 0);
  const tp1Crossed = tp1p > 0 && mark > 0 && (
    (pos.direction === 'Long'  && mark >= tp1p) ||
    (pos.direction === 'Short' && mark <= tp1p)
  );

  function distRow(label, price, cls) {
    if (!price) return '';
    const p    = parseFloat(price);
    const dist = ((p - mark) / mark * 100 * dir);  // positive = moving toward target
    const distStr = (dist >= 0 ? '+' : '') + dist.toFixed(2) + '%';
    const distCol = dist >= 0 ? 'color:var(--accent3)' : 'color:var(--red)';
    return `
      <div class="target-cell ${cls}">
        <div class="target-cell-label">${label}</div>
        <div class="target-cell-price" style="${cls.includes('sl') ? 'color:var(--red)' : 'color:var(--accent3)'}">${p}</div>
        <div class="target-cell-dist" style="${distCol}">${distStr} from mark</div>
      </div>`;
  }

  const entryDist = call.avg_entry
    ? (((parseFloat(call.avg_entry) - mark) / mark * 100 * dir * -1)).toFixed(2)
    : null;

  return `
    <div class="call-targets-panel">
      <h4>📡 Linked Call — ${call.trade_type || ''} · ${call.setup_score || '?'}/10 ${call.setup_label || ''} · R:R ${call.rr_ratio || '—'}</h4>
      <div class="targets-grid">
        ${call.tp1_price ? distRow('Take Profit 1', call.tp1_price, 'target-tp') : ''}
        ${call.tp2_price ? distRow('Take Profit 2', call.tp2_price, 'target-tp') : ''}
        ${call.sl_price  ? distRow('Stop Loss',      call.sl_price,  'target-sl') : ''}
        ${call.avg_entry ? `
        <div class="target-cell">
          <div class="target-cell-label">Call Avg Entry</div>
          <div class="target-cell-price" style="color:var(--accent2)">${parseFloat(call.avg_entry).toPrecision(5)}</div>
          <div class="target-cell-dist" style="${parseFloat(entryDist) >= 0 ? 'color:var(--accent3)' : 'color:var(--red)'}">
            ${parseFloat(entryDist) >= 0 ? '+' : ''}${entryDist}% from mark
          </div>
        </div>` : ''}
      </div>
      ${tp1Crossed ? `
        <div class="be-prompt">
          ✅ <strong>TP1 reached</strong> — consider moving Stop Loss to break-even (${beP > 0 ? beP.toPrecision(5) : 'entry price'}) to protect profits
        </div>` : ''}
      ${call.has_candle_close_sl ? `
        <div class="candle-sl-chip">⚠ Candle-close SL at ${call.sl_price} — monitor manually, close on 4H close below</div>` : ''}
      ${call.entry_timing ? `
        <div style="font-size:.75rem;color:var(--muted);margin-top:8px"><strong style="color:var(--text)">Entry timing:</strong> ${call.entry_timing}</div>` : ''}
      <div style="margin-top:10px;display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="closeCall(${call.id});loadLiveTrades()">Mark Call Closed</button>
      </div>
    </div>`;
}

function renderPositionCards(positions) {
  const container = document.getElementById('trades-container');
  if (!positions.length) {
    container.innerHTML = `<div class="no-positions">
      <div class="icon">😴</div>
      <div style="font-weight:600;margin-bottom:6px">No open positions</div>
      <div>All positions are closed. Good time to review your journal!</div>
    </div>`;
    return;
  }

  // Re-index open panels by symbol_dir key so they survive position reorder
  const openByKey = new Set(
    [...liveOpenPanels].map(i => {
      const old = livePositionsCache[i];
      return old ? old.symbol + '_' + old.direction : null;
    }).filter(Boolean)
  );
  liveOpenPanels.clear();

  container.innerHTML = positions.map((p, i) => {
    const key        = p.symbol + '_' + p.direction;
    const isCritical = p.unrealized_pct < -30;
    const isLoss     = p.unrealized_pnl < 0;
    const cardClass  = isCritical ? 'critical' : (isLoss ? 'loss' : 'profit');
    const pnlCol     = isLoss ? 'neg' : 'pos';
    const noSl       = !p.stop_loss;
    const noTp       = !p.take_profit;
    const dur        = p.duration_minutes != null ? durFmt(p.duration_minutes) : '—';
    const hadAnalysis = liveAnalysisCache[key];
    const is48h      = p.duration_minutes != null && p.duration_minutes > 2880;
    const liqDist    = (() => {
      const mark = parseFloat(p.mark_price || 0);
      const liq  = parseFloat(p.liquidation_price || 0);
      if (!liq || !mark) return Infinity;
      return Math.abs((liq - mark) / mark * 100);
    })();
    const isLiqNear  = liqDist < 15;

    return `
    <div class="position-card ${cardClass}" id="card-${i}">
      <div class="pos-header" onclick="togglePositionDetail(${i})">
        <!-- Symbol + badges -->
        <div>
          <div class="pos-symbol">${p.symbol}</div>
          <div class="pos-badge" style="margin-top:4px">
            <span class="badge ${p.direction.toLowerCase()}">${p.direction}</span>
            <span class="badge" style="background:rgba(108,99,255,.15);color:var(--accent)">${p.leverage}x</span>
            ${noSl ? '<span class="badge" style="background:rgba(239,83,80,.15);color:var(--red);font-size:.65rem">NO SL</span>' : ''}
            ${isCritical ? '<span class="badge" style="background:rgba(239,83,80,.25);color:var(--red);animation:pulse 1.5s infinite">⚠ CRITICAL</span>' : ''}
            ${is48h ? `<span class="chip-48h">⏱ ${Math.floor(p.duration_minutes/1440)}d+ OPEN</span>` : ''}
            ${isLiqNear ? `<span class="chip-liq-warn">⚡ LIQ ${liqDist.toFixed(1)}% AWAY</span>` : ''}
            ${(() => {
              const mc = liveMarketCtx[p.symbol] || {};
              const chips = [];
              const fr = mc.funding || {};
              if (fr.ok) {
                const col = fr.rate > 0 ? 'var(--yellow)' : 'var(--accent3)';
                const bg  = fr.rate > 0 ? 'rgba(255,179,0,.12)' : 'rgba(38,217,107,.12)';
                chips.push(`<span class="badge" style="background:${bg};color:${col};font-size:.65rem">F ${fr.rate_pct > 0 ? '+' : ''}${fr.rate_pct}%${fr.high ? ' ⚠' : ''}</span>`);
              }
              const ls = mc.long_short || {};
              if (ls.ok) {
                const crowded = ls.long_pct > 65 || ls.short_pct > 65;
                const col = crowded ? 'var(--yellow)' : 'var(--muted)';
                chips.push(`<span class="badge" style="background:rgba(121,134,203,.1);color:${col};font-size:.65rem">L/S ${ls.long_pct}/${ls.short_pct}</span>`);
              }
              return chips.join('');
            })()}
          </div>
        </div>
        <!-- Stats -->
        <div class="pos-stat">
          <div class="pos-stat-label">Size</div>
          <div class="pos-stat-val">${fmtC(p.size_usdt)} USDT</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Entry</div>
          <div class="pos-stat-val">${parseFloat(p.entry_price).toPrecision(5)}</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Mark Price</div>
          <div class="pos-stat-val">${parseFloat(p.mark_price).toPrecision(5)}</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Unrealized P&L</div>
          <div class="pos-stat-val ${pnlCol}">${p.unrealized_pnl>=0?'+':''}${fmtC(p.unrealized_pnl)} USDT</div>
          <div style="font-size:.7rem;${isLoss?'color:var(--red)':'color:var(--accent3)'}">${p.unrealized_pct>=0?'+':''}${p.unrealized_pct}%</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">TP / SL</div>
          <div class="pos-stat-val" style="font-size:.8rem">
            <span style="color:var(--accent3)">${p.take_profit || '—'}</span> /
            <span style="color:var(--red)">${p.stop_loss || '—'}</span>
          </div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Open</div>
          <div class="pos-stat-val" style="font-size:.8rem">${dur}</div>
        </div>
        <!-- AI button -->
        <div class="pos-actions">
          <button class="btn-ai-trade" id="ai-btn-${i}"
                  onclick="event.stopPropagation();analyzePosition(${i})">
            ${hadAnalysis ? '🔄 Re-analyze' : '🤖 AI Analysis'}
          </button>
        </div>
      </div>
      <!-- Expandable detail row -->
      <div style="padding:0 20px;display:none;font-size:.78rem;color:var(--muted);border-top:1px solid var(--border)" id="detail-${i}">
        <div style="display:flex;gap:24px;padding:10px 0;flex-wrap:wrap">
          <span>Break even: <strong>${parseFloat(p.break_even_price||0).toPrecision(5)}</strong></span>
          <span>Liquidation: <strong style="color:var(--red)">${parseFloat(p.liquidation_price||0).toPrecision(5)}</strong></span>
          <span>Margin: <strong>${fmtC(p.margin_usdt)} USDT (${p.margin_mode})</strong></span>
          <span>Fees paid: <strong>${fmtC(p.total_fee)}</strong></span>
          <span>Realized so far: <strong class="${pnlClass(p.achieved_profits)}">${p.achieved_profits>=0?'+':''}${fmtC(p.achieved_profits)}</strong></span>
        </div>
      </div>
      <!-- Call targets panel (shown when matched to a saved call) -->
      ${liveCallMatches[key] ? renderCallTargetsPanel(liveCallMatches[key], p) : ''}
      <!-- AI Analysis Panel — pre-open if it was open before the refresh -->
      <div class="pos-ai-panel${openByKey.has(key) ? ' open' : ''}" id="ai-panel-${i}"></div>
    </div>`;
  }).join('');

  // Restore cached AI analyses into the freshly rendered cards
  positions.forEach((p, i) => {
    const key = p.symbol + '_' + p.direction;
    if (liveAnalysisCache[key]) {
      renderTradeAnalysis(i, liveAnalysisCache[key]);
      liveOpenPanels.add(i);
    }
  });
}

function togglePositionDetail(i) {
  const d = document.getElementById(`detail-${i}`);
  d.style.display = d.style.display === 'none' ? 'flex' : 'none';
}

async function analyzePosition(idx) {
  const position = livePositionsCache[idx];
  if (!position) return;

  const btn   = document.getElementById(`ai-btn-${idx}`);
  const panel = document.getElementById(`ai-panel-${idx}`);

  btn.disabled  = true;
  btn.textContent = '⏳ Analyzing…';
  panel.className = 'pos-ai-panel open';
  panel.innerHTML = `<div class="ai-trade-loading">
    <div class="spinner"></div>
    <div>Claude is analyzing your ${position.symbol} ${position.direction} position…</div>
  </div>`;

  try {
    const res = await api('/api/live/analyze', 'POST', position);
    if (!res.ok) throw new Error(res.error);
    // Save to cache so the panel survives auto-refresh
    const key = position.symbol + '_' + position.direction;
    liveAnalysisCache[key] = res.data;
    liveOpenPanels.add(idx);
    renderTradeAnalysis(idx, res.data);
    btn.textContent = '🔄 Re-analyze';
    btn.disabled = false;
  } catch(e) {
    panel.innerHTML = `<div class="upload-result error" style="margin:16px">❌ ${e.message}</div>`;
    btn.textContent = '🤖 AI Analysis';
    btn.disabled = false;
  }
}

function renderTradeAnalysis(idx, d) {
  const panel = document.getElementById(`ai-panel-${idx}`);
  const risk  = d.risk_rating || {};
  const riskLabel = (risk.label || 'Unknown').toLowerCase().replace(' ', '');
  const actionClass = {
    'Hold': 'hold',
    'Close Now': 'close-now',
    'Partial Close': 'partial',
    'Adjust SL': 'partial',
  }[d.action] || 'hold';
  const urgClass = {
    'Immediate': 'urgency-immediate',
    'Today': 'urgency-today',
    'No rush': 'urgency-norush',
  }[d.time_urgency] || 'urgency-norush';

  const hist  = d._history || {};
  const tp    = d.tp_recommendation || {};
  const sl    = d.sl_recommendation || {};

  panel.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px">
      <span class="risk-badge risk-${riskLabel}">Risk ${risk.value}/10 · ${risk.label}</span>
      <span class="urgency-chip ${urgClass}">${d.time_urgency}</span>
      ${hist.trades ? `<span style="font-size:.75rem;color:var(--muted)">Based on ${hist.trades} past ${d._symbol} trades (${hist.win_rate_pct}% WR, ${hist.total_pnl >= 0 ? '+' : ''}${fmtC(hist.total_pnl)} USDT total)</span>` : ''}
    </div>

    <div class="action-box ${actionClass}">
      <div class="action-title">Recommended Action</div>
      <div class="action-val">${d.action}</div>
      <div style="font-size:.82rem;color:var(--muted);margin-top:4px">${d.action_reason || ''}</div>
    </div>

    <div class="ai-overall" style="margin:12px 0">${d.summary || ''}</div>

    ${(tp.price || sl.price) ? `
    <div class="tp-sl-grid">
      ${tp.price ? `<div class="tp-sl-card">
        <h4>🟢 Take Profit</h4>
        <div class="tp-sl-price tp">${tp.price}</div>
        <div class="tp-sl-rationale">${tp.rationale || ''}</div>
      </div>` : '<div></div>'}
      ${sl.price ? `<div class="tp-sl-card">
        <h4>🔴 Stop Loss</h4>
        <div class="tp-sl-price sl">${sl.price}</div>
        <div class="tp-sl-rationale">${sl.rationale || ''}</div>
      </div>` : '<div></div>'}
    </div>` : ''}

    ${d.key_risks?.length ? `
    <div style="margin-top:12px">
      <div style="font-size:.75rem;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:6px">Key Risks</div>
      <ul class="risk-list">${d.key_risks.map(r => `<li>${r}</li>`).join('')}</ul>
    </div>` : ''}

    ${d.historical_context ? `
    <div style="font-size:.78rem;color:var(--muted);margin-top:10px;padding:8px 12px;background:var(--bg3);border-radius:6px">
      📊 ${d.historical_context}
    </div>` : ''}

    <div style="font-size:.7rem;color:var(--border);margin-top:12px;text-align:right">
      ${d._input_tokens} in / ${d._output_tokens} out tokens · claude-sonnet-4-6
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// CORRELATION WARNING
// ══════════════════════════════════════════════════════════════════════════════
function renderCorrelationWarning(positions) {
  const el = document.getElementById('correlation-warning');
  if (!el) return;

  const SECTORS = {
    'Bitcoin':    ['BTCUSDT','WBTCUSDT'],
    'ETH / L2':   ['ETHUSDT','ARBUSDT','OPUSDT','MATICUSDT','STRKUSDT','ZKUSDT','SCROLLUSDT'],
    'SOL / L1':   ['SOLUSDT','AVAXUSDT','SUIUSDT','APTUSDT','NEARUSDT','SEIUSDT','INJUSDT'],
    'Meme':       ['DOGEUSDT','SHIBUSDT','PEPEUSDT','BOMEUSDT','WIFUSDT','BONKUSDT','FLOKIUSDT','MOGUSDT','POPCATUSDT'],
    'DeFi':       ['UNIUSDT','AAVEUSDT','CRVUSDT','MKRUSDT','SNXUSDT','COMPUSDT','DYDXUSDT'],
    'AI / Infra': ['FETUSDT','RENDERUSDT','WLDUSDT','TAOUSDT','AGIXUSDT','GRTUSDT'],
  };

  const warnings = [];

  // Sector correlation: 2+ positions in same sector, same direction
  for (const [sector, symbols] of Object.entries(SECTORS)) {
    for (const dir of ['Long', 'Short']) {
      const group = positions.filter(p => p.direction === dir && symbols.includes(p.symbol));
      if (group.length >= 2) {
        const exposure = group.reduce((s, p) => s + (p.size_usdt || p.margin_usdt || 0), 0);
        const severity = group.length >= 3 ? '🔴' : '🟡';
        warnings.push({
          severity: group.length >= 3 ? 2 : 1,
          text: `${severity} ${group.length}× ${dir} in <strong>${sector}</strong> sector `
              + `(${group.map(p=>p.symbol).join(', ')}) — `
              + `${fmtC(exposure)} USDT exposure moves together`,
        });
      }
    }
  }

  // Directional overload: 3+ positions all same direction across any sectors
  const longs  = positions.filter(p => p.direction === 'Long');
  const shorts  = positions.filter(p => p.direction === 'Short');
  if (longs.length >= 3) {
    const m = longs.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 2, text: `🔴 ${longs.length} simultaneous LONG positions — one BTC dump hits all of them (${fmtC(m)} USDT margin)` });
  } else if (longs.length === 2) {
    const m = longs.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 1, text: `🟡 2 simultaneous LONG positions (${fmtC(m)} USDT margin) — correlated downside risk` });
  }
  if (shorts.length >= 3) {
    const m = shorts.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 2, text: `🔴 ${shorts.length} simultaneous SHORT positions (${fmtC(m)} USDT margin) — correlated upside risk` });
  } else if (shorts.length === 2) {
    const m = shorts.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 1, text: `🟡 2 simultaneous SHORT positions (${fmtC(m)} USDT margin) — correlated upside risk` });
  }

  // Deduplicate and sort by severity
  const unique = [...new Map(warnings.map(w => [w.text, w])).values()]
    .sort((a, b) => b.severity - a.severity);

  if (unique.length) {
    const bg = unique.some(w => w.severity >= 2) ? 'rgba(239,83,80,.12)' : 'rgba(255,179,0,.10)';
    el.style.background = bg;
    el.innerHTML = unique.map(w => `<div style="margin-bottom:4px">${w.text}</div>`).join('');
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// PREDICTION ACCURACY
// ══════════════════════════════════════════════════════════════════════════════
async function loadPredictionAccuracy() {
  const res = await api('/api/calls/prediction-accuracy');
  const sec = document.getElementById('prediction-accuracy-section');
  const con = document.getElementById('prediction-accuracy-content');
  if (!res.ok || !res.data.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';
  con.innerHTML = `
    <div class="table-card">
      <div style="padding:12px 16px;font-size:.78rem;color:var(--muted)">
        Based on ${res.data.reduce((s,r)=>s+r.total,0)} recorded outcomes — shows whether setup scores predict actual results
      </div>
      <table class="tbl">
        <thead><tr>
          <th>Score Band</th><th>Calls</th><th>Wins</th><th>Losses</th>
          <th>Actual Win Rate</th><th>TP1 Hits</th><th>Avg PnL</th>
        </tr></thead>
        <tbody>${res.data.map(r => {
          const calibration = r.score_band.startsWith('8') ? 'Should be 70%+' :
                              r.score_band.startsWith('6') ? 'Should be 55-70%' :
                              r.score_band.startsWith('4') ? 'Should be 40-55%' : 'Should be <40%';
          const isCalibrated = (r.score_band.startsWith('8') && r.win_rate >= 65) ||
                               (r.score_band.startsWith('6') && r.win_rate >= 50) ||
                               (r.score_band.startsWith('4') && r.win_rate >= 35);
          return `<tr>
            <td><strong>${r.score_band}</strong></td>
            <td>${r.total}</td>
            <td class="pos">${r.wins}</td>
            <td class="neg">${r.losses}</td>
            <td class="${r.win_rate >= 55 ? 'pos' : r.win_rate >= 40 ? 'neu' : 'neg'}">
              <strong>${r.win_rate}%</strong>
              <span style="font-size:.7rem;color:var(--muted)"> · ${calibration}</span>
            </td>
            <td>${r.tp1_hits}</td>
            <td class="${pnlClass(r.avg_pnl)}">${r.avg_pnl != null ? (r.avg_pnl>=0?'+':'') + fmtC(r.avg_pnl) : '—'}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// POST-MORTEM LOSS ANALYSIS
// ══════════════════════════════════════════════════════════════════════════════
async function fetchAndShowPostmortem(callId) {
  const el = document.getElementById('postmortem-banner');
  if (!el) return;
  try {
    const res = await api('/api/calls/' + callId + '/postmortem');
    if (!res.ok || !res.data.findings.length) return;
    el.innerHTML = `
      <div class="pm-title">📋 Loss Post-Mortem — ${res.data.symbol}</div>
      ${res.data.findings.map(f => `<div style="margin-bottom:5px">• ${f}</div>`).join('')}
      <div style="margin-top:8px;font-size:.75rem;color:var(--muted)">Review these patterns before the next similar trade.</div>`;
    el.style.display = '';
    // Scroll to it
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch(e) {}
}

// ══════════════════════════════════════════════════════════════════════════════
// CALL OUTCOME RECORDING
// ══════════════════════════════════════════════════════════════════════════════
function openOutcomeModal(callId) {
  document.getElementById('outcome-call-id').value = callId;
  document.querySelectorAll('input[name="outcome-result"]').forEach(r => r.checked = false);
  document.getElementById('outcome-pnl').value = '';
  document.getElementById('outcome-hit-tp1').checked = false;
  document.getElementById('outcome-hit-tp2').checked = false;
  document.getElementById('outcome-hit-sl').checked  = false;
  document.getElementById('outcome-modal').classList.add('open');
}
function closeOutcomeModal() {
  document.getElementById('outcome-modal').classList.remove('open');
}
async function submitOutcome() {
  const callId  = document.getElementById('outcome-call-id').value;
  const outcome = document.querySelector('input[name="outcome-result"]:checked')?.value;
  if (!outcome) { alert('Please select a result (Won / Lost / Manual Close).'); return; }
  const body = {
    outcome,
    outcome_pnl: parseFloat(document.getElementById('outcome-pnl').value) || null,
    hit_tp1:     document.getElementById('outcome-hit-tp1').checked,
    hit_tp2:     document.getElementById('outcome-hit-tp2').checked,
    hit_sl:      document.getElementById('outcome-hit-sl').checked,
  };
  const res = await api('/api/calls/' + callId + '/record-outcome', 'POST', body);
  if (res.ok) {
    closeOutcomeModal();
    loadSavedCalls();
    loadAnalystStats();
    loadPredictionAccuracy();
    if (outcome === 'lost' || outcome === 'manual') {
      fetchAndShowPostmortem(callId);
    }
  } else alert('Error: ' + res.error);
}
document.getElementById('outcome-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeOutcomeModal();
});

// ══════════════════════════════════════════════════════════════════════════════
// MONTHLY TARGET TRACKER
// ══════════════════════════════════════════════════════════════════════════════
function setMonthlyTarget() {
  const val = parseFloat(document.getElementById('dash-target-input').value);
  if (!val || val <= 0) { alert('Enter a positive USDT target.'); return; }
  localStorage.setItem('monthly_pnl_target', val);
  updateMonthlyTargetUI();
}
function clearMonthlyTarget() {
  localStorage.removeItem('monthly_pnl_target');
  document.getElementById('dash-target-input').value = '';
  document.getElementById('dash-target-label').textContent = 'Set a target to track this month\'s progress';
  document.getElementById('dash-target-bar-wrap').style.display = 'none';
  document.getElementById('dash-target-clear').style.display = 'none';
}
function updateMonthlyTargetUI(currentPnl) {
  const target = parseFloat(localStorage.getItem('monthly_pnl_target') || 0);
  const label  = document.getElementById('dash-target-label');
  const barWrap= document.getElementById('dash-target-bar-wrap');
  const bar    = document.getElementById('dash-target-bar');
  const clear  = document.getElementById('dash-target-clear');
  if (!label) return;
  if (!target) { updateMonthlyTargetUI._pending = currentPnl; return; }
  document.getElementById('dash-target-input').value = target;
  clear.style.display = '';
  barWrap.style.display = '';
  if (currentPnl != null) {
    const pct  = Math.min(100, Math.max(0, currentPnl / target * 100));
    bar.style.width      = pct + '%';
    bar.style.background = currentPnl >= target ? 'var(--accent3)' : currentPnl >= 0 ? 'var(--accent)' : 'var(--red)';
    const rem  = target - currentPnl;
    label.innerHTML = `<span class="${pnlClass(currentPnl)}">${currentPnl >= 0 ? '+' : ''}${fmtC(currentPnl)} USDT</span>
      &nbsp;/ ${fmtC(target)} USDT (${pct.toFixed(0)}%)
      ${rem > 0 ? `<span style="color:var(--muted);font-size:.8rem"> · ${fmtC(rem)} to go</span>` : ' <span style="color:var(--accent3)">🎉 Target reached!</span>'}`;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// POSITION SIZE CHECKER
// ══════════════════════════════════════════════════════════════════════════════
function checkPositionSize(actual) {
  const panel = document.getElementById('size-checker-panel');
  const el    = document.getElementById('size-check-result');
  if (!panel || !el || !actual) { el && (el.textContent = ''); return; }
  const recommended = parseFloat(panel.dataset.recommended);
  const riskPct     = parseFloat(panel.dataset.risk);
  if (!recommended) return;
  const pct  = (actual - recommended) / recommended * 100;
  const diff = actual - recommended;
  if (Math.abs(pct) < 2) {
    el.style.color  = 'var(--accent3)';
    el.textContent  = `✅ On target — matches ${riskPct}% risk rule`;
  } else if (pct > 0) {
    el.style.color  = 'var(--red)';
    el.textContent  = `⚠ Over-sized by ${pct.toFixed(1)}% (+${diff.toFixed(0)} USDT) — exceeds ${riskPct}% risk target`;
  } else {
    el.style.color  = 'var(--yellow)';
    el.textContent  = `Under-sized by ${Math.abs(pct).toFixed(1)}% (${diff.toFixed(0)} USDT) — less risk than planned`;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// BITGET LIVE PENDING ORDERS
// ══════════════════════════════════════════════════════════════════════════════
let _bitgetOrdersCache = null;   // { entry:[], exit:[], tracked_ids:[], fetched_at }

async function loadBitgetOrders() {
  const content = document.getElementById('bitget-orders-content');
  const ageEl   = document.getElementById('bitget-orders-age');
  const btn     = document.getElementById('bitget-refresh-btn');
  if (!content) return;

  content.innerHTML = '<span style="color:var(--muted);font-size:.82rem">Fetching from Bitget…</span>';
  if (btn) btn.disabled = true;

  const res = await api('/api/live/pending-orders');
  if (btn) btn.disabled = false;

  if (!res.ok) {
    content.innerHTML = `<span style="color:var(--red);font-size:.82rem">❌ ${res.error}</span>`;
    return;
  }

  _bitgetOrdersCache = {
    entry:       res.data.bitget_orders?.entry  || [],
    exit:        res.data.bitget_orders?.exit   || [],
    tracked_ids: new Set(res.data.tracked_ids   || []),
    fetched_at:  new Date(),
  };

  renderBitgetOrdersSection();
  if (ageEl) ageEl.textContent = 'Updated just now';
}

function renderBitgetOrdersSection() {
  const content = document.getElementById('bitget-orders-content');
  if (!content || !_bitgetOrdersCache) return;

  const { entry, exit, tracked_ids } = _bitgetOrdersCache;

  if (!entry.length && !exit.length) {
    content.innerHTML = '<span style="color:var(--muted);font-size:.82rem">No pending limit orders on Bitget right now.</span>';
    return;
  }

  let html = '';

  if (entry.length) {
    html += `<div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:8px">Entry Orders (${entry.length})</div>`;
    html += entry.map(o => renderBitgetOrderRow(o, tracked_ids, false)).join('');
  }

  if (exit.length) {
    html += `<div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;font-weight:700;margin:14px 0 8px">Exit / TP-SL Orders (${exit.length})</div>`;
    html += exit.map(o => renderBitgetOrderRow(o, tracked_ids, true)).join('');
  }

  content.innerHTML = html;
}

function renderBitgetOrderRow(o, trackedIds, isExit) {
  const tracked  = trackedIds.has(o.order_id);
  const dirClass = o.direction === 'Long' ? 'long' : 'short';
  const rowClass = isExit ? 'bitget-order-row exit-order-row' : 'bitget-order-row';

  const trackBtn = (!isExit && !tracked)
    ? `<button class="btn btn-sm" style="background:rgba(108,99,255,.15);color:var(--accent);border:1px solid rgba(108,99,255,.3);white-space:nowrap" onclick='openMatchModal(${JSON.stringify(o)})'>🔗 Track &amp; Match</button>`
    : (tracked ? `<span class="tracked-badge">✓ Tracked</span>` : '');

  const notional = o.notional_usdt ? `<span style="color:var(--yellow)">${fmtC(o.notional_usdt)} USDT</span>` : '';
  const lev      = o.leverage ? `<span style="font-size:.75rem;color:var(--muted)">${o.leverage}x</span>` : '';

  return `<div class="${rowClass}">
    <span style="font-weight:700;min-width:90px">${escHtml(o.symbol)}</span>
    <span class="badge ${dirClass}">${o.direction}</span>
    ${isExit ? `<span style="font-size:.72rem;background:rgba(239,83,80,.12);color:var(--red);padding:2px 8px;border-radius:20px">Exit</span>` : ''}
    <span style="color:var(--accent2);font-weight:600">@ ${o.price}</span>
    ${notional} ${lev}
    <span style="font-size:.75rem;color:var(--muted);margin-left:4px">${o.created_at}</span>
    <span style="margin-left:auto">${trackBtn}</span>
  </div>`;
}

let _matchOrderData = null;

async function openMatchModal(order) {
  _matchOrderData = order;
  document.getElementById('match-order-id').value     = order.order_id;
  document.getElementById('match-sl-price').value     = '';
  document.getElementById('match-tp1-price').value    = '';
  document.getElementById('match-tp2-price').value    = '';
  document.getElementById('match-notes').value        = '';
  document.getElementById('match-selected-call-id').value = '';

  // Build order summary card
  document.getElementById('match-order-summary').innerHTML = `
    <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
      <span style="font-size:1.1rem;font-weight:700">${escHtml(order.symbol)}</span>
      <span class="badge ${order.direction==='Long'?'long':'short'}">${order.direction}</span>
      <div><div style="font-size:.7rem;color:var(--muted)">Limit Price</div><div style="font-weight:700;color:var(--accent2)">${order.price}</div></div>
      ${order.notional_usdt ? `<div><div style="font-size:.7rem;color:var(--muted)">Notional</div><div style="font-weight:700;color:var(--yellow)">${fmtC(order.notional_usdt)} USDT</div></div>` : ''}
      ${order.leverage ? `<div><div style="font-size:.7rem;color:var(--muted)">Leverage</div><div style="font-weight:700">${order.leverage}x</div></div>` : ''}
      <div style="margin-left:auto;font-size:.75rem;color:var(--muted)">${order.created_at}</div>
    </div>`;

  // Load saved calls for picker
  const picker = document.getElementById('match-call-picker');
  picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">Loading…</div>';

  const res = await api('/api/calls/saved');
  if (!res.ok || !res.data.length) {
    picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">No saved calls found. Analyze and save a call first, or track without linking.</div>';
  } else {
    // Sort: same symbol+direction first
    const sym = order.symbol;
    const dir = order.direction;
    const sorted = [...res.data].sort((a, b) => {
      const aMatch = (a.symbol === sym && a.direction === dir) ? 0 : 1;
      const bMatch = (b.symbol === sym && b.direction === dir) ? 0 : 1;
      return aMatch - bMatch;
    });

    picker.innerHTML = sorted.map(c => {
      const isMatch = c.symbol === sym && c.direction === dir;
      return `<div class="outcome-option" id="call-pick-${c.id}" onclick="selectCallForMatch(${c.id})" style="${isMatch ? 'border-color:var(--accent);' : ''}">
        <input type="radio" name="match-call-radio" value="${c.id}" style="width:14px;height:14px;accent-color:var(--accent)">
        <div style="flex:1">
          <div style="font-weight:600;font-size:.85rem">
            ${escHtml(c.symbol)} ${c.direction}
            ${isMatch ? '<span style="color:var(--accent3);font-size:.72rem"> ✓ matches this order</span>' : ''}
          </div>
          <div style="font-size:.75rem;color:var(--muted)">
            Score: ${c.setup_score||'—'}/10 · ${c.trade_type||''} ·
            SL: ${c.sl_price||'—'} · TP1: ${c.tp1_price||'—'}
            ${c.analyst ? ' · 📡 '+escHtml(c.analyst) : ''}
            · ${(c.created_at||'').slice(0,10)}
          </div>
        </div>
      </div>`;
    }).join('');
  }

  document.getElementById('match-modal').classList.add('open');
}

function selectCallForMatch(callId) {
  document.getElementById('match-selected-call-id').value = callId;
  // Tick the radio + highlight
  document.querySelectorAll('input[name="match-call-radio"]').forEach(r => {
    r.checked = (parseInt(r.value) === callId);
  });
  document.querySelectorAll('#match-call-picker .outcome-option').forEach(el => {
    el.style.background = '';
  });
  const picked = document.getElementById('call-pick-' + callId);
  if (picked) picked.style.background = 'rgba(108,99,255,.08)';
}

function closeMatchModal() {
  document.getElementById('match-modal').classList.remove('open');
  _matchOrderData = null;
}

async function saveMatchedOrder() {
  if (!_matchOrderData) return;
  const o       = _matchOrderData;
  const callId  = document.getElementById('match-selected-call-id').value || null;

  // Look up analyst from the selected call
  let analyst = '';
  if (callId) {
    const radio = document.querySelector(`input[name="match-call-radio"][value="${callId}"]`);
    const callRow = radio?.closest('.outcome-option');
    if (callRow) {
      const analystSpan = callRow.querySelector('.analyst-tag');
      if (analystSpan) analyst = analystSpan.textContent.replace('📡','').trim();
    }
    // Try from the summary text
    const matchText = callRow?.textContent || '';
    const m = matchText.match(/📡\s*([^\s·]+)/);
    if (m) analyst = m[1].trim();
  }

  const body = {
    symbol:          o.symbol,
    direction:       o.direction,
    limit_price:     o.price,
    size_usdt:       o.notional_usdt || null,
    leverage:        parseInt(o.leverage) || 10,
    sl_price:        parseFloat(document.getElementById('match-sl-price').value)  || null,
    tp1_price:       parseFloat(document.getElementById('match-tp1-price').value) || null,
    tp2_price:       parseFloat(document.getElementById('match-tp2-price').value) || null,
    notes:           document.getElementById('match-notes').value.trim(),
    call_id:         callId ? parseInt(callId) : null,
    analyst,
    bitget_order_id: o.order_id,
  };

  const res = await api('/api/limits', 'POST', body);
  if (res.ok) {
    closeMatchModal();
    loadBitgetOrders();
    loadPendingLimits('waiting');
  } else {
    alert('Error: ' + res.error);
  }
}

document.getElementById('match-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeMatchModal();
});

async function openLinkCallModal(limitId) {
  // Re-use match modal for linking an existing pending_limit to a call
  _matchOrderData = { _existing_limit_id: limitId };

  // Fetch the limit to get its symbol/direction
  const res = await api('/api/limits?status=waiting');
  const lim  = (res.data || []).find(l => l.id === limitId);
  if (!lim) return;

  document.getElementById('match-order-id').value = '';
  document.getElementById('match-sl-price').value  = lim.sl_price  || '';
  document.getElementById('match-tp1-price').value = lim.tp1_price || '';
  document.getElementById('match-tp2-price').value = lim.tp2_price || '';
  document.getElementById('match-notes').value     = lim.notes     || '';
  document.getElementById('match-selected-call-id').value = '';

  document.getElementById('match-order-summary').innerHTML = `
    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">
      <span style="font-size:1rem;font-weight:700">${escHtml(lim.symbol)}</span>
      <span class="badge ${lim.direction==='Long'?'long':'short'}">${lim.direction}</span>
      <div><div style="font-size:.7rem;color:var(--muted)">Limit Price</div><div style="font-weight:700;color:var(--accent2)">${lim.limit_price}</div></div>
      ${lim.size_usdt ? `<div><div style="font-size:.7rem;color:var(--muted)">Size</div><div style="font-weight:700;color:var(--yellow)">${fmtC(lim.size_usdt)} USDT</div></div>` : ''}
      <span style="font-size:.75rem;color:var(--muted);margin-left:auto">Limit #${limitId}</span>
    </div>`;

  // Load call picker (same as openMatchModal)
  const picker = document.getElementById('match-call-picker');
  picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">Loading…</div>';

  const callsRes = await api('/api/calls/saved');
  if (!callsRes.ok || !callsRes.data.length) {
    picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">No saved calls found.</div>';
  } else {
    const sym = lim.symbol; const dir = lim.direction;
    const sorted = [...callsRes.data].sort((a, b) =>
      ((a.symbol===sym&&a.direction===dir)?0:1) - ((b.symbol===sym&&b.direction===dir)?0:1)
    );
    picker.innerHTML = sorted.map(c => {
      const isMatch = c.symbol === sym && c.direction === dir;
      return `<div class="outcome-option" id="call-pick-${c.id}" onclick="selectCallForMatch(${c.id})" style="${isMatch?'border-color:var(--accent);':''}">
        <input type="radio" name="match-call-radio" value="${c.id}" style="width:14px;height:14px;accent-color:var(--accent)">
        <div style="flex:1">
          <div style="font-weight:600;font-size:.85rem">${escHtml(c.symbol)} ${c.direction}
            ${isMatch?'<span style="color:var(--accent3);font-size:.72rem"> ✓ matches</span>':''}
          </div>
          <div style="font-size:.75rem;color:var(--muted)">Score: ${c.setup_score||'—'}/10 · ${c.trade_type||''} · SL: ${c.sl_price||'—'} · TP1: ${c.tp1_price||'—'}${c.analyst?' · 📡 '+escHtml(c.analyst):''} · ${(c.created_at||'').slice(0,10)}</div>
        </div>
      </div>`;
    }).join('');
  }

  document.getElementById('match-modal').classList.add('open');
}

// Override saveMatchedOrder to handle existing limit case
const _origSaveMatchedOrder = saveMatchedOrder;
saveMatchedOrder = async function() {
  if (_matchOrderData?._existing_limit_id) {
    // Linking an existing limit to a call
    const limitId = _matchOrderData._existing_limit_id;
    const callId  = document.getElementById('match-selected-call-id').value;
    const updates = {
      call_id:   callId ? parseInt(callId) : null,
      sl_price:  parseFloat(document.getElementById('match-sl-price').value)  || null,
      tp1_price: parseFloat(document.getElementById('match-tp1-price').value) || null,
      tp2_price: parseFloat(document.getElementById('match-tp2-price').value) || null,
      notes:     document.getElementById('match-notes').value.trim(),
    };
    const res = await api('/api/limits/' + limitId, 'PATCH', updates);
    if (res.ok) { closeMatchModal(); loadPendingLimits('waiting'); }
    else alert('Error: ' + res.error);
    return;
  }
  await _origSaveMatchedOrder();
};

// ══════════════════════════════════════════════════════════════════════════════
// PENDING LIMIT ORDERS
// ══════════════════════════════════════════════════════════════════════════════
let currentLimitStatus = 'waiting';

async function loadPendingLimits(status) {
  currentLimitStatus = status || 'waiting';
  // Update tab button highlights
  ['waiting','triggered','cancelled'].forEach(s => {
    const btn = document.getElementById('pending-tab-' + s);
    if (!btn) return;
    btn.style.borderColor = s === currentLimitStatus ? 'var(--accent)' : '';
    btn.style.color       = s === currentLimitStatus ? 'var(--accent)' : '';
  });

  const list = document.getElementById('pending-limits-list');
  if (!list) return;
  list.innerHTML = '<div class="ai-loading"><div class="spinner"></div><p>Loading…</p></div>';

  if (currentLimitStatus === 'waiting') loadPendingRiskSummary();
  else { const b = document.getElementById('pending-risk-banner'); if (b) b.style.display = 'none'; }

  const res = await api('/api/limits?status=' + currentLimitStatus);
  if (!res.ok) { list.innerHTML = '<p style="color:var(--red);padding:20px">Error loading orders</p>'; return; }

  if (!res.data.length) {
    const msgs = {
      waiting:   'No pending limit orders. Click "+ Add Limit Order" to track a trade setup.',
      triggered: 'No triggered limits yet.',
      cancelled: 'No cancelled limits.',
    };
    list.innerHTML = `<div class="no-positions"><div class="icon">⏳</div><p style="color:var(--muted)">${msgs[currentLimitStatus]}</p></div>`;
    return;
  }
  list.innerHTML = res.data.map(renderPendingLimitCard).join('');
}

async function loadPendingRiskSummary() {
  const banner = document.getElementById('pending-risk-banner');
  if (!banner) return;
  const res = await api('/api/limits/risk-summary');
  if (!res.ok || !res.data.pending_count) { banner.style.display = 'none'; return; }
  const d = res.data;
  banner.style.display = '';
  const syms = d.by_symbol.map(s => `${s.symbol}: <strong>${fmtC(s.notional)} USDT</strong>`).join(' · ');
  banner.innerHTML = `<strong>⚠ ${d.pending_count} pending order${d.pending_count!==1?'s':''} · ${fmtC(d.total_notional_usdt)} USDT notional committed if all fill</strong>${syms ? `<div style="margin-top:4px;font-size:.75rem;opacity:.85">${syms}</div>` : ''}`;
}

function renderPendingLimitCard(lim) {
  const chipClass = 'limit-chip-' + lim.status;
  const chipLabel = {waiting:'⏳ Waiting', triggered:'✅ Triggered', cancelled:'✕ Cancelled'}[lim.status] || lim.status;
  const dirClass  = lim.direction === 'Long' ? 'long' : 'short';

  // Stop distance & risk
  let riskStr = '';
  if (lim.limit_price && lim.sl_price && lim.size_usdt) {
    const stopDist = Math.abs(lim.limit_price - lim.sl_price) / lim.limit_price * 100;
    const riskUsdt = lim.size_usdt * stopDist / 100;
    riskStr = `Risk if SL: <span class="neg">${fmtC(riskUsdt)} USDT</span> (${stopDist.toFixed(1)}% stop)`;
  }

  // R:R to TP1
  let rrStr = '';
  if (lim.tp1_price && lim.sl_price && lim.limit_price) {
    const gain = Math.abs(lim.tp1_price - lim.limit_price);
    const loss = Math.abs(lim.limit_price - lim.sl_price);
    if (loss > 0) rrStr = ` · R:R <strong style="color:var(--accent)">${(gain/loss).toFixed(1)}:1</strong>`;
  }

  // Stored AI verdict
  let verdictHtml = '';
  if (lim.analysis_json) {
    try {
      const a = JSON.parse(lim.analysis_json);
      const vColor = a.verdict==='Keep' ? 'var(--accent3)' : a.verdict==='Adjust' ? 'var(--yellow)' : 'var(--red)';
      verdictHtml = `<div class="pending-verdict">
        <span style="font-weight:700;color:${vColor}">${a.verdict}</span>
        <span style="color:var(--muted)"> · Score ${a.setup_score}/10 · ${a.confidence} confidence</span>
        <div style="margin-top:6px;color:var(--muted);font-size:.8rem">${escHtml(a.summary||'')}</div>
        ${a.adjustments?.length ? `<div style="margin-top:8px;font-size:.78rem">${a.adjustments.map(x=>`<div style="padding:3px 0;border-bottom:1px solid var(--border)">→ ${escHtml(x)}</div>`).join('')}</div>` : ''}
        ${a.risks?.length ? `<div style="margin-top:6px;font-size:.75rem;color:var(--red)">${a.risks.map(r=>`<div>⚠ ${escHtml(r)}</div>`).join('')}</div>` : ''}
      </div>`;
    } catch(e) {}
  }

  const waitingActions = lim.status === 'waiting' ? `
    <button class="btn btn-sm" style="background:linear-gradient(135deg,#6c63ff,#4fc3f7);color:#fff;border:none" onclick="analyzePendingLimit(${lim.id})">🤖 AI Analysis</button>
    ${!lim.call_id ? `<button class="btn btn-sm" style="background:rgba(108,99,255,.12);color:var(--accent);border:1px solid rgba(108,99,255,.3)" onclick="openLinkCallModal(${lim.id})">🔗 Link Call</button>` : ''}
    <button class="btn btn-sm btn-secondary" onclick="openLimitModal(${lim.id})">✏ Edit</button>
    <button class="btn btn-sm" style="background:rgba(38,217,107,.15);color:var(--accent3);border:1px solid rgba(38,217,107,.3)" onclick="markLimitTriggered(${lim.id})">✅ Mark Triggered</button>
    <button class="btn btn-sm btn-danger" onclick="cancelPendingLimit(${lim.id})">✕ Cancel</button>` :
    `<button class="btn btn-sm btn-danger" onclick="deleteLimit(${lim.id})">🗑 Delete</button>`;

  return `<div class="pending-card ${lim.status}" id="pending-card-${lim.id}">
    <div class="pending-card-header">
      <span style="font-size:1rem;font-weight:700">${escHtml(lim.symbol)}</span>
      <span class="badge ${dirClass}">${lim.direction}</span>
      <span class="${chipClass}">${chipLabel}</span>
      ${lim.bitget_order_id ? `<span style="font-size:.65rem;background:rgba(79,195,247,.12);color:var(--accent2);padding:2px 8px;border-radius:20px;font-weight:700">⚡ Bitget</span>` : ''}
      ${lim.call_id ? `<span style="font-size:.65rem;background:rgba(108,99,255,.12);color:var(--accent);padding:2px 8px;border-radius:20px;font-weight:700">📡 Linked Call #${lim.call_id}</span>` : ''}
      ${lim.analyst ? `<span style="font-size:.75rem;color:var(--muted)">${escHtml(lim.analyst)}</span>` : ''}
      <div style="display:flex;gap:16px;align-items:center;margin-left:auto;flex-wrap:wrap">
        ${lim.limit_price ? `<div class="pos-stat"><div class="pos-stat-label">Limit</div><div class="pos-stat-val" style="color:var(--accent2)">${lim.limit_price}</div></div>` : ''}
        ${lim.sl_price    ? `<div class="pos-stat"><div class="pos-stat-label">SL</div><div class="pos-stat-val" style="color:var(--red)">${lim.sl_price}</div></div>` : ''}
        ${lim.tp1_price   ? `<div class="pos-stat"><div class="pos-stat-label">TP1</div><div class="pos-stat-val" style="color:var(--accent3)">${lim.tp1_price}</div></div>` : ''}
        ${lim.tp2_price   ? `<div class="pos-stat"><div class="pos-stat-label">TP2</div><div class="pos-stat-val" style="color:var(--accent3)">${lim.tp2_price}</div></div>` : ''}
        ${lim.size_usdt   ? `<div class="pos-stat"><div class="pos-stat-label">Size</div><div class="pos-stat-val" style="color:var(--yellow)">${fmtC(lim.size_usdt)} USDT</div></div>` : ''}
        ${lim.leverage    ? `<div class="pos-stat"><div class="pos-stat-label">Lev</div><div class="pos-stat-val">${lim.leverage}x</div></div>` : ''}
      </div>
    </div>
    ${(riskStr || rrStr || lim.notes || lim.triggered_at) ? `<div style="padding:0 18px 8px;font-size:.75rem;color:var(--muted)">
      ${riskStr}${rrStr}
      ${lim.notes ? `<span style="margin-left:8px">· ${escHtml(lim.notes)}</span>` : ''}
      ${lim.triggered_at ? `<span style="margin-left:8px">· Triggered: ${lim.triggered_at.slice(0,16)}</span>` : ''}
      <span style="margin-left:8px">· Added: ${(lim.created_at||'').slice(0,10)}</span>
    </div>` : ''}
    ${verdictHtml}
    <div id="pending-analyze-${lim.id}" style="display:none;padding:0 18px 12px"></div>
    <div style="display:flex;gap:8px;padding:10px 18px 14px;flex-wrap:wrap">${waitingActions}</div>
  </div>`;
}

function openLimitModal(limitId, prefill) {
  const fields = ['lm-symbol','lm-limit-price','lm-size-usdt','lm-sl-price','lm-tp1-price','lm-tp2-price','lm-analyst','lm-notes'];
  fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  document.getElementById('lm-leverage').value   = '10';
  document.getElementById('lm-direction').value  = 'Long';
  document.getElementById('limit-modal-id').value    = limitId || '';
  document.getElementById('limit-modal-call-id').value = (prefill && prefill.call_id) ? prefill.call_id : '';

  if (prefill) {
    if (prefill.symbol)        document.getElementById('lm-symbol').value        = prefill.symbol;
    if (prefill.direction)     document.getElementById('lm-direction').value     = prefill.direction;
    if (prefill.sl_price)      document.getElementById('lm-sl-price').value      = prefill.sl_price;
    if (prefill.tp1_price)     document.getElementById('lm-tp1-price').value     = prefill.tp1_price;
    if (prefill.tp2_price)     document.getElementById('lm-tp2-price').value     = prefill.tp2_price;
    if (prefill.entry_price)   document.getElementById('lm-limit-price').value   = prefill.entry_price;
    if (prefill.total_notional)document.getElementById('lm-size-usdt').value     = prefill.total_notional;
    if (prefill.analyst)       document.getElementById('lm-analyst').value       = prefill.analyst;
    if (prefill.leverage)      document.getElementById('lm-leverage').value      = prefill.leverage;
  }

  if (limitId && !prefill) {
    api('/api/limits?status=waiting').then(res => {
      const lim = (res.data||[]).find(l => l.id === limitId);
      if (!lim) return;
      document.getElementById('lm-symbol').value        = lim.symbol;
      document.getElementById('lm-direction').value     = lim.direction;
      document.getElementById('lm-limit-price').value   = lim.limit_price;
      if (lim.size_usdt)  document.getElementById('lm-size-usdt').value  = lim.size_usdt;
      if (lim.leverage)   document.getElementById('lm-leverage').value   = lim.leverage;
      if (lim.sl_price)   document.getElementById('lm-sl-price').value   = lim.sl_price;
      if (lim.tp1_price)  document.getElementById('lm-tp1-price').value  = lim.tp1_price;
      if (lim.tp2_price)  document.getElementById('lm-tp2-price').value  = lim.tp2_price;
      if (lim.analyst)    document.getElementById('lm-analyst').value    = lim.analyst;
      if (lim.notes)      document.getElementById('lm-notes').value      = lim.notes;
    });
  }
  document.getElementById('limit-modal').classList.add('open');
}

function closeLimitModal() {
  document.getElementById('limit-modal').classList.remove('open');
}

async function saveLimitOrder() {
  const limitId    = document.getElementById('limit-modal-id').value;
  const symbol     = document.getElementById('lm-symbol').value.trim().toUpperCase();
  const limitPrice = parseFloat(document.getElementById('lm-limit-price').value);
  if (!symbol || !limitPrice) { alert('Symbol and Limit Price are required.'); return; }

  const body = {
    symbol,
    direction:  document.getElementById('lm-direction').value,
    limit_price: limitPrice,
    size_usdt:  parseFloat(document.getElementById('lm-size-usdt').value)  || null,
    leverage:   parseInt(document.getElementById('lm-leverage').value)     || 10,
    sl_price:   parseFloat(document.getElementById('lm-sl-price').value)   || null,
    tp1_price:  parseFloat(document.getElementById('lm-tp1-price').value)  || null,
    tp2_price:  parseFloat(document.getElementById('lm-tp2-price').value)  || null,
    analyst:    document.getElementById('lm-analyst').value.trim(),
    notes:      document.getElementById('lm-notes').value.trim(),
    call_id:    document.getElementById('limit-modal-call-id').value || null,
  };

  const res = limitId
    ? await api('/api/limits/' + limitId, 'PATCH', body)
    : await api('/api/limits', 'POST', body);

  if (res.ok) {
    closeLimitModal();
    if (currentPage === 'pending') loadPendingLimits(currentLimitStatus);
  } else alert('Error: ' + res.error);
}

async function analyzePendingLimit(limitId) {
  const analyzeDiv = document.getElementById('pending-analyze-' + limitId);
  if (analyzeDiv) {
    analyzeDiv.style.display = '';
    analyzeDiv.innerHTML = '<div style="padding:12px;color:var(--muted);display:flex;align-items:center;gap:8px"><div class="spinner" style="width:18px;height:18px;border-width:2px;margin:0"></div> Analyzing with Claude…</div>';
  }
  const res = await api('/api/limits/' + limitId + '/analyze', 'POST');
  if (analyzeDiv) analyzeDiv.style.display = 'none';
  if (!res.ok) { alert('Analysis error: ' + res.error); return; }
  loadPendingLimits(currentLimitStatus);
}

async function markLimitTriggered(limitId) {
  if (!confirm('Mark this limit as triggered / filled?')) return;
  const res = await api('/api/limits/' + limitId, 'PATCH', { status: 'triggered' });
  if (res.ok) loadPendingLimits(currentLimitStatus);
  else alert('Error: ' + res.error);
}

async function cancelPendingLimit(limitId) {
  if (!confirm('Cancel this pending limit order?')) return;
  const res = await api('/api/limits/' + limitId, 'PATCH', { status: 'cancelled' });
  if (res.ok) loadPendingLimits(currentLimitStatus);
  else alert('Error: ' + res.error);
}

async function deleteLimit(limitId) {
  if (!confirm('Permanently delete this limit record?')) return;
  const res = await api('/api/limits/' + limitId, 'DELETE');
  if (res.ok) loadPendingLimits(currentLimitStatus);
  else alert('Error: ' + res.error);
}

document.getElementById('limit-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeLimitModal();
});

// ══════════════════════════════════════════════════════════════════════════════
// ANALYST LEADERBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function loadAnalystStats() {
  const res = await api('/api/calls/analyst-stats');
  const sec = document.getElementById('analyst-stats-section');
  const con = document.getElementById('analyst-stats-content');
  if (!res.ok || !res.data.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const medals = ['🥇','🥈','🥉'];

  function edgeBadge(score) {
    if (score == null) return '<span style="color:var(--muted);font-size:.75rem">need 3+ trades</span>';
    const color = score >= 65 ? 'var(--accent3)' : score >= 45 ? 'var(--yellow)' : 'var(--red)';
    const bg    = score >= 65 ? 'rgba(38,217,107,.15)' : score >= 45 ? 'rgba(255,179,0,.15)' : 'rgba(239,83,80,.15)';
    return `<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:.8rem;font-weight:800;background:${bg};color:${color}">${score}</span>`;
  }

  con.innerHTML = `
    <div class="table-card">
      <div style="font-size:.72rem;color:var(--muted);padding:8px 16px 4px">
        Sorted by Edge Score (0-100) · 50% trade win rate + 30% call outcome win rate + 20% TP1 hit rate · Needs 3+ closed trades to score
      </div>
      <table class="tbl">
        <thead><tr>
          <th>#</th><th>Analyst</th>
          <th title="Edge Score (0-100 composite)">Edge</th>
          <th title="Closed trades in journal">Trades</th>
          <th title="Journal win rate">Win %</th>
          <th title="Total realized PnL">Total PnL</th>
          <th title="Average PnL per trade">Avg PnL</th>
          <th title="Calls recorded as won vs SL hit">Call W/L</th>
          <th title="TP1 hit rate from recorded outcomes">TP1 Rate</th>
          <th title="How often you enter their calls">Entry Rate</th>
          <th title="Avg setup score">Score</th>
          <th title="Waiting limits">Pending</th>
        </tr></thead>
        <tbody>${res.data.map((a, i) => {
          const ranked = a.edge_score != null;
          const rowStyle = ranked && a.edge_score >= 65 ? 'background:rgba(38,217,107,.04)'
                         : ranked && a.edge_score < 45  ? 'background:rgba(239,83,80,.04)' : '';
          return `<tr style="${rowStyle}">
            <td style="color:var(--muted);font-size:.9rem">${medals[i] || (i+1)}</td>
            <td><strong>${escHtml(a.analyst)}</strong></td>
            <td>${edgeBadge(a.edge_score)}</td>
            <td>${a.trade_count || 0}</td>
            <td class="${a.win_rate >= 50 ? 'pos' : 'neg'}">${a.trade_count > 0 ? a.win_rate + '%' : '—'}</td>
            <td class="${pnlClass(a.total_pnl)}">${a.trade_count > 0 ? pnlSign(a.total_pnl) + fmtC(a.total_pnl) : '—'}</td>
            <td class="${pnlClass(a.avg_pnl)}">${a.trade_count > 0 ? pnlSign(a.avg_pnl) + fmtC(a.avg_pnl) : '—'}</td>
            <td>${a.call_win_rate != null ? `<span class="pos">${a.call_win_rate}%</span>` : '—'}</td>
            <td>${a.tp1_hit_rate != null ? `<span class="${a.tp1_hit_rate >= 50 ? 'pos' : 'neg'}">${a.tp1_hit_rate}%</span>` : '—'}</td>
            <td style="color:var(--muted)">${a.conv_rate != null ? a.conv_rate + '%' : '—'}</td>
            <td style="color:var(--muted)">${a.avg_setup_score != null ? a.avg_setup_score + '/10' : '—'}</td>
            <td>${a.pending_count > 0 ? `<span style="color:var(--accent2)">${a.pending_count}</span>` : '—'}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// LIVE SYNC
// ══════════════════════════════════════════════════════════════════════════════
let syncPolling = null;

async function pollSyncStatus() {
  try {
    const res = await api('/api/sync/status');
    if (!res.ok) return;
    const s = res.data;

    const dot   = document.getElementById('sync-dot');
    const label = document.getElementById('sync-label');
    const eq    = document.getElementById('sync-equity');

    if (s.running) {
      dot.className   = 'sync-dot syncing';
      label.textContent = 'Syncing with Bitget…';
    } else if (s.last_error) {
      dot.className   = 'sync-dot error';
      label.textContent = 'Sync error — ' + s.last_error.slice(0, 60);
    } else if (s.last_run) {
      dot.className   = 'sync-dot';
      label.textContent = 'Live · Last sync: ' + s.last_run;
    } else {
      dot.className   = 'sync-dot syncing';
      label.textContent = 'First sync starting…';
    }

    if (s.account_equity) {
      const val = parseFloat(s.account_equity).toFixed(2);
      eq.textContent = '⚡ ' + val + ' USDT';
      // Live page cards
      document.getElementById('live-equity').textContent    = val + ' USDT';
      document.getElementById('live-last').textContent      = s.last_run || '—';
      document.getElementById('live-next').textContent      = 'Next: ' + (s.next_run || '—');
    }
    if (s.available_balance) {
      document.getElementById('live-available').textContent =
        parseFloat(s.available_balance).toFixed(2) + ' USDT';
    }
  } catch(e) {}
}

async function triggerSync(fromLivePage = false) {
  const btn = fromLivePage
    ? document.getElementById('live-btn-sync')
    : document.getElementById('btn-sync');
  const msg = document.getElementById('live-sync-msg');

  btn.disabled = true;
  document.getElementById('btn-sync').disabled = true;
  if (msg) msg.textContent = 'Syncing…';
  document.getElementById('sync-dot').className = 'sync-dot syncing';
  document.getElementById('sync-label').textContent = 'Syncing with Bitget…';

  try {
    const res = await api('/api/sync', 'POST');
    if (res.ok) {
      const d = res.data;
      const total = (d.positions||0) + (d.orders||0) + (d.bills||0);
      if (msg) msg.textContent = `✅ Sync complete — ${d.positions} new positions, ${d.orders} orders, ${d.bills} bills`;

      // Update result table on live page
      document.getElementById('live-result-tbody').innerHTML = `
        <tr><td style="color:var(--muted)">New positions</td><td class="pos">+${d.positions}</td></tr>
        <tr><td style="color:var(--muted)">New orders</td><td>+${d.orders}</td></tr>
        <tr><td style="color:var(--muted)">New bills</td><td>+${d.bills}</td></tr>
        <tr><td style="color:var(--muted)">Synced at</td><td>${d.synced_at}</td></tr>`;

      // Refresh dashboard if new data arrived
      if (total > 0 && currentPage === 'dashboard') loadDashboard();
      if (total > 0 && currentPage === 'journal')   journalLoad(journalPage);
    } else {
      if (msg) msg.textContent = '❌ ' + res.error;
    }
  } catch(e) {
    if (msg) msg.textContent = '❌ ' + e.message;
  } finally {
    btn.disabled = false;
    document.getElementById('btn-sync').disabled = false;
    pollSyncStatus();
  }
}

// showPage extension for live pages
const _origShowPage = showPage;
showPage = function(name) {
  const extras = ['live', 'trades', 'calls', 'pending'];
  if (extras.includes(name)) {
    document.querySelectorAll('.page-view').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    document.getElementById('nav-' + name).classList.add('active');
    currentPage = name;
    if (name === 'live')    pollSyncStatus();
    if (name === 'calls')   { loadCallEquity(); loadSavedCalls(); loadAnalystStats(); loadPredictionAccuracy(); }
    if (name === 'pending') { loadBitgetOrders(); loadPendingLimits('waiting'); }
    if (name === 'trades') {
      loadLiveTrades();
      // Auto-refresh every 30s while on this page
      if (liveTradesInterval) clearInterval(liveTradesInterval);
      liveTradesInterval = setInterval(() => {
        if (currentPage === 'trades') loadLiveTrades();
      }, 30000);
    }
    return;
  }
  // Stop live trades refresh when leaving the page
  if (liveTradesInterval) { clearInterval(liveTradesInterval); liveTradesInterval = null; }
  _origShowPage(name);
};

// ── Initial load ───────────────────────────────────────────────────────────────
loadDashboard();
// Poll sync status every 30s so the sync bar stays current
pollSyncStatus();
syncPolling = setInterval(pollSyncStatus, 30000);
