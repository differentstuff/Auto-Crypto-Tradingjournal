// ══════════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function loadDashboard() {
  // Fetch KPIs, market context, and live positions in parallel
  const [res, mr, lr] = await Promise.all([
    api('/api/dashboard/kpis'),
    api('/api/market/context?symbols=BTCUSDT'),
    api('/api/live/positions'),
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
    `${d.total_trades} closed positions · Win rate ${d.win_rate}% · Profit factor ${d.profit_factor ?? '—'}`;

  // KPI cards
  const kpis = [
    { label: 'Total Realized P&L', value: (d.total_pnl >= 0 ? '+' : '') + fmtC(d.total_pnl) + ' USDT',
      cls: pnlClass(d.total_pnl), sub: `Net after fees`,
      tip: 'Sum of all closed trade profits and losses after deducting exchange fees. This is your actual earned money.' },
    { label: 'Total Fees', value: fmtC(d.total_fees) + ' USDT', cls: 'neg', sub: 'Paid to exchange',
      tip: 'Total trading fees paid to Bitget across all trades — opening and closing fees combined.' },
    { label: 'Win Rate', value: d.win_rate + '%', cls: d.win_rate >= 50 ? 'pos' : 'neg',
      sub: `${d.win_trades}W / ${d.loss_trades}L`,
      tip: 'Percentage of trades that closed in profit. Above 50% = more winners than losers. Profit factor matters too — a 40% win rate can still be profitable with large winners.' },
    { label: 'Profit Factor', value: d.profit_factor ?? '—', cls: d.profit_factor > 1 ? 'pos' : 'neg',
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
