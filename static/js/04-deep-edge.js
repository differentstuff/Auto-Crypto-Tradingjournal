
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

  const res = await api('/api/analytics/patterns', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(exchFilters()),
  });
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
  const res = await api('/api/analytics/deep?' + new URLSearchParams(exchFilters()));
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
  const hmRes = await api('/api/analytics/heatmap?' + new URLSearchParams(exchFilters()));
  if (hmRes.ok) renderHeatmap(hmRes.data);

  // P&L by Setup Type breakdown (new endpoint with profit factor + avg win/loss)
  loadSetupBreakdown('?' + new URLSearchParams(exchFilters()));

  // BTC benchmark comparison
  loadBenchmark();

  // Professional Performance Metrics (quantstats summary)
  loadTearsheetSummary();
}

async function loadTearsheetSummary() {
    const el = document.getElementById('tearsheet-summary');
    if (!el) return;
    try {
        const r = await fetch('/api/analytics/tearsheet');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = d.data?.reason || 'Need 20+ trading days of wallet history.';
            return;
        }
        const m = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px';
        [
            ['Sharpe',       m.sharpe != null ? m.sharpe.toFixed(2) : '--', m.sharpe >= 1 ? 'pnl-pos' : m.sharpe < 0 ? 'pnl-neg' : ''],
            ['Max Drawdown', m.max_drawdown_pct + '%', 'pnl-neg'],
            ['CAGR',         (m.cagr_pct >= 0 ? '+' : '') + m.cagr_pct + '%', m.cagr_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Volatility',   m.volatility_pct + '%/yr', ''],
            ['Daily Win%',   m.win_rate_daily + '%', m.win_rate_daily >= 50 ? 'pnl-pos' : 'pnl-neg'],
            ['Total Return', (m.total_return_pct >= 0 ? '+' : '') + m.total_return_pct + '%', m.total_return_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:10px;border-radius:6px;text-align:center';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:10px;color:var(--text-muted,#888);text-transform:uppercase';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:16px;font-weight:700;margin-top:4px';
            val.textContent = value;
            if (cls) val.className = cls;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        el.appendChild(grid);
        const link = document.createElement('a');
        link.href = '/api/analytics/tearsheet/download';
        link.target = '_blank';
        link.className = 'btn btn-secondary';
        link.textContent = 'Download Full Tearsheet (HTML)';
        el.appendChild(link);
    } catch(e) {
        if (el) el.textContent = 'Could not load tearsheet.';
    }
}

// Fetch setup type breakdown — uses DOM methods, no innerHTML
async function loadSetupBreakdown(filters) {
    const el = document.getElementById('setup-breakdown-body');
    if (!el) return;
    try {
        const r = await fetch('/api/analytics/by-setup' + (filters || ''));
        const d = await r.json();
        const setups = (d.data || {}).setups || [];
        if (!setups.length) {
            el.textContent = 'No setup-type data yet. Tag your trades with setup types in the journal.';
            return;
        }
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Setup Type','Trades','Total P&L','Win %','Avg P&L','Avg Win','Avg Loss','Prof. Factor'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        setups.forEach(s => {
            const tr = tbody.insertRow();
            const pf = s.profit_factor === 999 ? 'INF' : (s.profit_factor != null ? s.profit_factor.toFixed(2) : '-');
            const cells = [
                {v: s.setup_type, cls: ''},
                {v: s.trade_count, cls: ''},
                {v: (s.total_pnl >= 0 ? '+' : '') + '$' + (s.total_pnl || 0).toFixed(2), cls: s.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'},
                {v: (s.win_rate || 0) + '%', cls: ''},
                {v: (s.avg_pnl >= 0 ? '+' : '') + '$' + (s.avg_pnl || 0).toFixed(2), cls: s.avg_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'},
                {v: '+$' + (s.avg_win || 0).toFixed(2), cls: 'pnl-pos'},
                {v: '-$' + Math.abs(s.avg_loss || 0).toFixed(2), cls: 'pnl-neg'},
                {v: pf, cls: (s.profit_factor || 0) >= 1.5 ? 'pnl-pos' : ''},
            ];
            cells.forEach(({v, cls}) => {
                const td = tr.insertCell();
                td.textContent = v;   // textContent — server numeric values
                if (cls) td.className = cls;
            });
        });
        while (el.firstChild) el.removeChild(el.firstChild);
        el.appendChild(tbl);
    } catch(e) {
        if (el) el.textContent = 'Could not load setup breakdown.';
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// BTC BENCHMARK COMPARISON
// ══════════════════════════════════════════════════════════════════════════════
async function loadBenchmark() {
  const el = document.getElementById('benchmark-body');
  if (!el) return;

  try {
    const res = await api('/api/analytics/benchmark');
    if (!res.ok) {
      el.textContent = 'Could not load benchmark data.';
      return;
    }
    const d = res.data;

    if (!d.available) {
      el.textContent = 'No closed trades yet. Benchmark will appear once you have trade history.';
      return;
    }

    // 3-column metric grid
    const grid = document.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px';

    const metrics = [
      { label: 'Your Return', value: (d.trader_return_pct >= 0 ? '+' : '') + d.trader_return_pct + '%',
        cls: d.trader_return_pct >= 0 ? 'pos' : 'neg' },
      { label: 'BTC Return',  value: (d.btc_return_pct  >= 0 ? '+' : '') + d.btc_return_pct  + '%',
        cls: d.btc_return_pct  >= 0 ? 'pos' : 'neg' },
      { label: 'Alpha',       value: (d.alpha_pct       >= 0 ? '+' : '') + d.alpha_pct       + '%',
        cls: d.alpha_pct       >= 0 ? 'pos' : 'neg' },
    ];

    metrics.forEach(m => {
      const cell = document.createElement('div');
      cell.style.cssText = 'background:var(--bg3);border-radius:6px;padding:12px;text-align:center';

      const lbl = document.createElement('div');
      lbl.style.cssText = 'font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px';
      lbl.textContent = m.label;

      const val = document.createElement('div');
      val.style.cssText = 'font-size:1.3rem;font-weight:700';
      val.className = m.cls;
      val.textContent = m.value;

      cell.appendChild(lbl);
      cell.appendChild(val);
      grid.appendChild(cell);
    });

    // Note line
    const note = document.createElement('div');
    note.style.cssText = 'font-size:.75rem;color:var(--muted)';
    const capital = Number(d.assumed_capital).toLocaleString('en-US', { maximumFractionDigits: 0 });
    note.textContent = d.period_days + '-day period · assumed capital $' + capital + ' USDT · BTC buy-and-hold from ' + (d.start_date || '—') + ' to ' + (d.end_date || '—');

    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(grid);
    el.appendChild(note);

  } catch (e) {
    if (el) el.textContent = 'Could not load benchmark data.';
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// EDGE LAB
// ══════════════════════════════════════════════════════════════════════════════
async function loadEdge() {
  loadRulebook();
  const res = await api('/api/analytics/deep?' + new URLSearchParams(exchFilters()));
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
  const rrRes = await api('/api/analytics/rr?' + new URLSearchParams(exchFilters()));
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
// TRADER RULEBOOK
// ══════════════════════════════════════════════════════════════════════════════

async function loadRulebook() {
  const res = await api('/api/rulebook');
  if (!res.ok) return;
  renderRulebook(res.data);
}

async function updateRulebook() {
  const btn = document.getElementById('rulebook-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Generating rules…';
  document.getElementById('rulebook-results').innerHTML =
    '<div style="color:var(--muted);font-size:.85rem;padding:12px">Analysing your trade history — this takes ~10 seconds…</div>';

  try {
    const res = await api('/api/rulebook/update', 'POST', {});
    if (!res.ok) throw new Error(res.error);
    renderRulebook(res.data);
  } catch(e) {
    document.getElementById('rulebook-results').innerHTML =
      `<div class="upload-result error">Error: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '⚡ Generate / Update Rulebook';
  }
}

function renderRulebook(data) {
  const el = document.getElementById('rulebook-results');
  const ts = document.getElementById('rulebook-updated');

  if (data.insufficient_data) {
    el.innerHTML = `<div style="color:var(--muted);font-size:.85rem;padding:12px">${data.message}</div>`;
    return;
  }
  if (!data.rules || !data.rules.length) {
    el.innerHTML = `<div style="color:var(--muted);font-size:.85rem;padding:12px">No rules yet — click Generate to build your rulebook.</div>`;
    return;
  }

  if (data.updated_at) ts.textContent = `Last updated: ${data.updated_at}`;

  const typeConfig = {
    warning:     { color: 'var(--red)',     icon: '⚠', label: 'WARNING' },
    strength:    { color: 'var(--accent3)', icon: '✓', label: 'STRENGTH' },
    habit:       { color: 'var(--yellow)',  icon: '→', label: 'HABIT' },
    calibration: { color: 'var(--accent2)', icon: '~', label: 'CALIBRATION' },
  };

  const confColor = { high: 'var(--accent3)', medium: 'var(--yellow)', low: 'var(--muted)' };

  el.innerHTML = `<div style="display:flex;flex-direction:column;gap:10px">` +
    data.rules.map(r => {
      const rtype = r.rule_type || r.type || '';
      const cfg = typeConfig[rtype] || { color: 'var(--muted)', icon: '•', label: rtype.toUpperCase() || 'RULE' };
      const conf = r.confidence || 'medium';
      return `
        <div class="ai-item" style="border-left:3px solid ${cfg.color};padding-left:12px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap">
            <span style="color:${cfg.color};font-size:.72rem;font-weight:700;letter-spacing:.05em">${cfg.icon} ${cfg.label}</span>
            <span style="font-weight:600;font-size:.88rem">${r.title}</span>
            <span style="margin-left:auto;font-size:.72rem;color:${confColor[conf]}">${conf} confidence · ${r.data_points} trades</span>
          </div>
          <div style="font-size:.84rem;color:var(--fg)">${r.rule}</div>
        </div>`;
    }).join('') + `</div>`;
}
