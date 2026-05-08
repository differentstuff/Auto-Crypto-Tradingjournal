// ══════════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function loadDashboard() {
  // Fetch KPIs, market context, live positions, and new stats in parallel
  const [res, mr, lr, scRes, rollRes] = await Promise.all([
    api('/api/dashboard/kpis?' + new URLSearchParams(exchFilters())),
    api('/api/market/context?symbols=BTCUSDT'),
    api('/api/live/positions'),
    api('/api/analytics/sharpe-calmar?' + new URLSearchParams(exchFilters())),
    api('/api/analytics/rolling?' + new URLSearchParams(exchFilters())),
  ]);
  if (!res.ok) return;
  const d = res.data;

  // Market Pulse
  ;(mr => {
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
  })(mr);

  document.getElementById('dash-subtitle').textContent =
    `${d.total_trades} closed positions · Win rate ${d.win_rate}% · Profit factor ${d.profit_factor >= 999 ? '∞' : (d.profit_factor ?? '—')}`;

  const fmtPF = v => v == null ? '—' : v >= 999 ? '∞' : v;
  // KPI cards
  const kpis = [
    { label: 'Total Realized P&L', value: (d.total_pnl >= 0 ? '+' : '') + fmtC(d.total_pnl) + ' USDT',
      cls: pnlClass(d.total_pnl), sub: `Net after fees`,
      tip: 'Sum of all closed trade profits and losses after deducting exchange fees. This is your actual earned money.' },
    { label: 'Total Fees', value: fmtC(d.total_fees) + ' USDT', cls: 'neg', sub: 'Paid to exchange',
      tip: 'Total trading fees paid to exchanges across all trades — opening and closing fees combined.' },
    { label: 'Win Rate', value: d.win_rate + '%', cls: d.win_rate >= 50 ? 'pos' : 'neg',
      sub: `${d.win_trades}W / ${d.loss_trades}L`,
      tip: 'Percentage of trades that closed in profit. Above 50% = more winners than losers. Profit factor matters too — a 40% win rate can still be profitable with large winners.' },
    { label: 'Profit Factor', value: fmtPF(d.profit_factor), cls: d.profit_factor > 1 ? 'pos' : 'neg',
      sub: 'Gross wins / losses',
      tip: 'Gross profit divided by gross loss. Above 1.5 = strong edge. Above 1.0 = profitable overall. Below 1.0 = losing system.' },
    { label: 'Best Trade', value: '+' + fmtC(d.best_trade) + ' USDT', cls: 'pos',
      tip: 'Your single highest-profit closed trade.' },
    { label: 'Worst Trade', value: fmtC(d.worst_trade) + ' USDT', cls: 'neg',
      tip: 'Your single biggest losing closed trade.' },
    { label: 'Avg Win', value: '+' + fmtC(d.avg_win) + ' USDT', cls: 'pos',
      tip: 'Average profit on winning trades. Compare to Avg Loss — a higher ratio means positive expectancy even with a sub-50% win rate.' },
    { label: 'Avg Loss', value: fmtC(d.avg_loss) + ' USDT', cls: 'neg',
      tip: 'Average loss on losing trades. Ideally smaller than Avg Win. If larger, your system needs a high win rate to be profitable.' },
    { label: 'Max Drawdown', value: fmtC(d.max_drawdown) + ' USDT', cls: 'neg',
      sub: 'Peak-to-trough on PnL curve',
      tip: 'Largest peak-to-trough decline in your cumulative PnL curve. Measures how deep you went underwater before recovering. Key risk metric.' },
    { label: 'Total Trades', value: d.total_trades, cls: 'neu',
      tip: 'Total number of closed positions imported into the journal. More trades = more statistical significance for your metrics.' },
  ];
  document.getElementById('kpi-grid').innerHTML = kpis.map(k => `
    <div class="kpi-card"${k.tip ? ` data-tip="${k.tip}"` : ''}>
      <div class="kpi-label">${k.label}</div>
      <div class="kpi-value ${k.cls||''}">${k.value}</div>
      ${k.sub ? `<div class="kpi-sub">${k.sub}</div>` : ''}
    </div>`).join('');

  // Sharpe / Calmar cards (appended separately to avoid re-rendering the full grid)
  if (scRes.ok && scRes.data.sharpe != null) {
    const sc   = scRes.data;
    const grid = document.getElementById('kpi-grid');
    [
      { label: 'Sharpe Ratio',
        value: sc.sharpe,
        sub:   'Ann. vol ' + sc.ann_volatility_pct + '%',
        cls:   sc.sharpe >= 1 ? 'pos' : sc.sharpe > 0 ? 'neu' : 'neg',
        tip:   'Annualised Sharpe from daily wallet returns. ≥1 good, ≥2 excellent.' },
      { label: 'Calmar Ratio',
        value: sc.calmar ?? '—',
        sub:   'Max DD ' + sc.max_drawdown_pct + '%',
        cls:   sc.calmar >= 1 ? 'pos' : 'neu',
        tip:   'Annualised return / max drawdown. ≥1 = you earned back your worst DD in a year.' },
    ].forEach(k => {
      const card = document.createElement('div');
      card.className = 'kpi-card';
      if (k.tip) card.dataset.tip = k.tip;
      const lbl = document.createElement('div');
      lbl.className = 'kpi-label';
      lbl.textContent = k.label;
      const val = document.createElement('div');
      val.className = 'kpi-value ' + (k.cls || '');
      val.textContent = k.value;
      card.appendChild(lbl);
      card.appendChild(val);
      if (k.sub) {
        const sub = document.createElement('div');
        sub.className = 'kpi-sub';
        sub.textContent = k.sub;
        card.appendChild(sub);
      }
      grid.appendChild(card);
    });
  }

  // Rolling 30-day comparison row
  if (rollRes.ok) {
    const ro  = rollRes.data;
    const el  = document.getElementById('rolling-stats-row');
    if (el && ro.rolling && ro.all_time) {
      const r = ro.rolling, a = ro.all_time;
      const pnlCls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
      const diff = v => {
        if (v == null) return '';
        const n = parseFloat(v);
        return n > 0 ? `<span class="pos">+${n}</span>` : `<span class="neg">${n}</span>`;
      };
      el.style.display = 'block';
      el.querySelector('.rolling-label').textContent = `Last ${ro.days} days`;
      el.querySelector('.rolling-wr').textContent    = (r.win_rate ?? '—') + '%';
      el.querySelector('.rolling-pnl').textContent   = (r.total_pnl >= 0 ? '+' : '') + fmtC(r.total_pnl) + ' USDT';
      el.querySelector('.rolling-pnl').className     = 'rolling-pnl kpi-value ' + pnlCls(r.total_pnl);
      el.querySelector('.rolling-trades').textContent = r.trades + ' trades';
    }
  }

  // Open position risk — already fetched in parallel above
  if (lr.ok) {
    const pos = lr.data.positions || [];
    const eq  = parseFloat(lr.data.equity?.accountEquity || 0);
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
      return s + (p.margin_usdt || 0);
    }, 0);
    const riskPct = eq > 0 ? (totalRisk / eq * 100).toFixed(1) : 0;
    const hasSl   = pos.some(p => parseFloat(p.stop_loss || 0) > 0);
    const riskEl  = document.createElement('div');
    riskEl.className = 'kpi-card';
    riskEl.dataset.tip = 'Maximum loss if all stop-losses hit simultaneously. Calculated as (entry − SL) / entry × position size. Positions without SL use full margin as a conservative estimate.';
    riskEl.innerHTML = `
      <div class="kpi-label">Open Position Risk</div>
      <div class="kpi-value ${totalRisk > 0 ? 'neg' : 'neu'}">${fmtC(totalRisk)} USDT</div>
      <div class="kpi-sub">${riskPct}% of equity · ${pos.length} open${hasSl ? ' · SL-based' : ' · no SL'}</div>`;
    document.getElementById('kpi-grid').appendChild(riskEl);
  }

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

  // Wallet chart with drawdown overlay
  if (d.wallet_curve.length) {
    const wc = d.wallet_curve;
    // Compute drawdown from peak as a percentage at each point
    let peak = wc[0].wallet_balance || 0;
    const ddPct = wc.map(p => {
      const b = p.wallet_balance || 0;
      if (b > peak) peak = b;
      return peak > 0 ? parseFloat(((b - peak) / peak * 100).toFixed(2)) : 0;
    });
    makeChart('walletChart', 'line', {
      labels: wc.map(p => p.date.slice(0,10)),
      datasets: [
        {
          label: 'Wallet Balance (USDT)',
          data:  wc.map(p => p.wallet_balance),
          borderColor: '#4fc3f7', backgroundColor: 'rgba(79,195,247,.08)',
          fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
          yAxisID: 'yBalance',
        },
        {
          label: 'Drawdown %',
          data:  ddPct,
          borderColor: 'rgba(239,83,80,.7)', backgroundColor: 'rgba(239,83,80,.08)',
          fill: true, tension: 0.1, pointRadius: 0, borderWidth: 1.5,
          yAxisID: 'yDD',
        },
      ]
    }, {
      plugins: { legend: { display: true, position: 'bottom',
        labels: { color: '#7986cb', font: { size: 10 } } } },
      scales: {
        x:        { ticks: { color: '#7986cb', font: { size: 10 } }, grid: { color: 'rgba(45,50,80,.6)' } },
        yBalance: { type: 'linear', position: 'left',
                    ticks: { color: '#4fc3f7', font: { size: 10 } }, grid: { color: 'rgba(45,50,80,.6)' } },
        yDD:      { type: 'linear', position: 'right',
                    ticks: { color: 'rgba(239,83,80,.8)', font: { size: 9 },
                             callback: v => v + '%' },
                    grid: { drawOnChartArea: false } },
      },
    });
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
