// ══════════════════════════════════════════════════════════════════════════════
// HINDSIGHT ANALYSIS — retroactive trade scoring & comparison
// ══════════════════════════════════════════════════════════════════════════════

let _hindsightPoll = null;

async function loadHindsight() {
  const res = await api('/api/hindsight/status');
  if (!res.ok) return;
  renderHindsightStatus(res.data);
  if (res.data.status === 'running') _startHindsightPoller();

  const r2 = await api('/api/hindsight/results');
  if (r2.ok && r2.data.rows && r2.data.rows.length) renderHindsightResults(r2.data);
}

async function runHindsight(n) {
  const btn = document.getElementById('btn-hindsight');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Starting…'; }
  const res = await api('/api/hindsight/run?n=' + n, 'POST');
  if (!res.ok) { if (btn) { btn.disabled = false; btn.textContent = '🔮 Analyze'; } return; }
  renderHindsightStatus(res.data);
  _startHindsightPoller();
}

function _startHindsightPoller() {
  if (_hindsightPoll) clearInterval(_hindsightPoll);
  _hindsightPoll = setInterval(async () => {
    const s = await api('/api/hindsight/status');
    if (!s.ok) return;
    renderHindsightStatus(s.data);
    if (s.data.status !== 'running') {
      clearInterval(_hindsightPoll); _hindsightPoll = null;
      const r = await api('/api/hindsight/results');
      if (r.ok) renderHindsightResults(r.data);
    }
  }, 2000);
}

// ── Status bar ────────────────────────────────────────────────────────────────

function renderHindsightStatus(state) {
  const el = document.getElementById('hindsight-status');
  if (!el) return;
  const running  = state.status === 'running';
  const done     = state.status === 'completed';
  const prog     = state.progress || 0;
  const total    = state.total || 0;
  const pct      = total ? Math.round(prog / total * 100) : 0;

  let bar = '';
  if (running) {
    bar = `<div style="background:var(--bg3);border-radius:4px;height:6px;width:100%;max-width:320px;margin-top:6px;overflow:hidden">
      <div style="background:var(--accent);height:100%;width:${pct}%;transition:width .4s"></div></div>
      <div style="font-size:.75rem;color:var(--muted);margin-top:4px">${prog}/${total} trades analyzed…</div>`;
  }

  const btnLabel = running ? '⏳ Running…' : '🔮 Analyze Last 50 Trades';
  const dur = done && state.duration_sec ? ` · completed in ${state.duration_sec}s` : '';
  const msg = running ? `<span style="color:var(--yellow)">Running — ${prog}/${total} analyzed</span>`
            : done    ? `<span style="color:var(--muted)">Done${dur}</span>`
            : state.status === 'error' ? `<span style="color:var(--red)">Error: ${state.error}</span>`
            : `<span style="color:var(--muted)">No analysis run yet.</span>`;

  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <button class="btn btn-primary" id="btn-hindsight"
        onclick="runHindsight(50)" ${running ? 'disabled' : ''}>${btnLabel}</button>
      <button class="btn btn-secondary btn-sm" onclick="runHindsight(25)">25 trades</button>
      <button class="btn btn-secondary btn-sm" onclick="runHindsight(100)">100 trades</button>
      <span style="font-size:.82rem">${msg}</span>
    </div>${bar}`;
}

// ── Results ───────────────────────────────────────────────────────────────────

function renderHindsightResults(data) {
  const { rows, summary } = data;
  if (!rows || !rows.length) return;

  const sumEl = document.getElementById('hindsight-summary');
  const tblEl = document.getElementById('hindsight-table');
  if (sumEl) sumEl.innerHTML = renderHindsightSummary(summary, rows.length);
  if (tblEl) tblEl.innerHTML = renderHindsightTable(rows);
}

function renderHindsightSummary(s, n) {
  if (!s) return '';
  const pnlDiff = s.hyp_total_pnl - s.actual_total_pnl;
  const diffCol = pnlDiff >= 0 ? 'var(--accent3)' : 'var(--red)';
  const diffSign = pnlDiff >= 0 ? '+' : '';
  const sigAcc = s.signal_accuracy != null ? `${s.signal_accuracy}%` : '—';

  const insight = _buildInsight(s, pnlDiff);

  return `
  <div class="card" style="margin-bottom:20px">
    <div style="font-size:.95rem;font-weight:700;margin-bottom:14px;color:var(--text)">
      Comparison — Last ${n} Trades
    </div>

    <div class="hindsight-comparison">
      <!-- Actual column -->
      <div class="hc-col">
        <div class="hc-col-header">Actual (what happened)</div>
        <div class="hc-kpi">${s.total}<div class="hc-kpi-lbl">trades</div></div>
        <div class="hc-kpi ${s.actual_win_rate >= 55 ? 'pos' : s.actual_win_rate >= 45 ? 'neu' : 'neg'}">
          ${s.actual_win_rate}%<div class="hc-kpi-lbl">win rate</div></div>
        <div class="hc-kpi ${s.actual_total_pnl >= 0 ? 'pos' : 'neg'}">
          ${s.actual_total_pnl >= 0 ? '+' : ''}${fmtC(s.actual_total_pnl)}
          <div class="hc-kpi-lbl">total P&L</div></div>
        <div class="hc-kpi">${s.avg_score_all}<div class="hc-kpi-lbl">avg score</div></div>
      </div>

      <!-- Arrow -->
      <div style="display:flex;align-items:center;font-size:1.4rem;color:var(--muted);padding-top:32px">→</div>

      <!-- Recommended column -->
      <div class="hc-col">
        <div class="hc-col-header">Following Recommendations (score ≥ 7)</div>
        <div class="hc-kpi">${s.hyp_trades_taken}<div class="hc-kpi-lbl">entered</div></div>
        <div class="hc-kpi ${s.hyp_win_rate >= 55 ? 'pos' : s.hyp_win_rate >= 45 ? 'neu' : 'neg'}">
          ${s.hyp_win_rate}%<div class="hc-kpi-lbl">win rate</div></div>
        <div class="hc-kpi ${s.hyp_total_pnl >= 0 ? 'pos' : 'neg'}">
          ${s.hyp_total_pnl >= 0 ? '+' : ''}${fmtC(s.hyp_total_pnl)}
          <div class="hc-kpi-lbl">total P&L</div></div>
        <div class="hc-kpi" style="color:${diffCol};font-size:1.1rem;font-weight:700">
          ${diffSign}${fmtC(pnlDiff)}<div class="hc-kpi-lbl">P&L difference</div></div>
      </div>

      <!-- Signal accuracy column -->
      <div class="hc-col">
        <div class="hc-col-header">Signal Accuracy</div>
        <div class="hc-kpi ${s.signal_accuracy >= 65 ? 'pos' : s.signal_accuracy >= 50 ? 'neu' : 'neg'}">
          ${sigAcc}<div class="hc-kpi-lbl">accuracy</div></div>
        <div style="font-size:.75rem;color:var(--muted);line-height:1.7;margin-top:8px">
          ✅ TP: <strong style="color:var(--accent3)">${s.tp}</strong> — entered, trade won<br>
          ❌ FP: <strong style="color:var(--red)">${s.fp}</strong> — entered, trade lost<br>
          ✅ TN: <strong style="color:var(--accent3)">${s.tn}</strong> — skipped, trade lost<br>
          ❌ FN: <strong style="color:var(--yellow)">${s.fn}</strong> — skipped, trade won
        </div>
      </div>

      <!-- Score vs outcome column -->
      <div class="hc-col">
        <div class="hc-col-header">Score vs Outcome</div>
        <div style="font-size:.78rem;color:var(--muted);margin-top:8px;line-height:1.9">
          Avg score of <strong style="color:var(--accent3)">winners</strong>: ${s.avg_score_winners}<br>
          Avg score of <strong style="color:var(--red)">losers</strong>:  ${s.avg_score_losers}<br>
          High-conviction (≥8): <strong style="color:var(--text)">${s.high_conf_count} trades</strong><br>
          Win rate at ≥8: <strong style="color:${s.high_conf_win_rate >= 60 ? 'var(--accent3)' : 'var(--red)'}">
            ${s.high_conf_win_rate}%</strong><br>
          Skipped (score&lt;5): <strong style="color:var(--muted)">${s.hyp_trades_skipped}</strong>
          ${s.skipped_pnl !== 0 ? `<span style="color:${s.skipped_pnl < 0 ? 'var(--accent3)' : 'var(--red)'}">
            (${s.skipped_pnl <= 0 ? 'saved ' : 'missed '}${fmtC(Math.abs(s.skipped_pnl))})</span>` : ''}
        </div>
      </div>
    </div>

    ${insight ? `<div class="hindsight-insight">${insight}</div>` : ''}
  </div>`;
}

function _buildInsight(s, pnlDiff) {
  const parts = [];
  if (pnlDiff > 50)
    parts.push(`📈 Following recommendations would have added <strong>${fmtC(pnlDiff)} USDT</strong>.`);
  else if (pnlDiff < -50)
    parts.push(`📉 Following recommendations would have cost <strong>${fmtC(Math.abs(pnlDiff))} USDT</strong> — the skipped trades were winners.`);

  const wr_diff = s.hyp_win_rate - s.actual_win_rate;
  if (Math.abs(wr_diff) >= 3)
    parts.push(`Win rate would have ${wr_diff > 0 ? 'improved' : 'dropped'} by ${Math.abs(wr_diff).toFixed(1)}pp.`);

  if (s.skipped_pnl < -30)
    parts.push(`Skipping ${s.hyp_trades_skipped} low-conviction setups would have saved <strong>${fmtC(Math.abs(s.skipped_pnl))} USDT</strong>.`);

  const scoreDiff = s.avg_score_winners - s.avg_score_losers;
  if (scoreDiff >= 1.5)
    parts.push(`Winners averaged score ${s.avg_score_winners} vs ${s.avg_score_losers} for losers — scoring system is predictive.`);
  else if (scoreDiff < 0.5)
    parts.push(`Score gap between winners (${s.avg_score_winners}) and losers (${s.avg_score_losers}) is small — market conditions may have overridden setup quality.`);

  if (!parts.length) return '';
  return parts.join(' ');
}

// ── Trade table ───────────────────────────────────────────────────────────────

function renderHindsightTable(rows) {
  const hdr = `<div class="hindsight-row hindsight-hdr">
    <div>Trade</div>
    <div>Date</div>
    <div>Actual P&L</div>
    <div>Score</div>
    <div>Rec.</div>
    <div>Hyp. P&L</div>
    <div>Δ</div>
    <div>Verdict</div>
  </div>`;

  const bodyRows = rows.map(r => {
    const score     = r.setup_score || 0;
    const scoreCol  = score >= 8 ? 'var(--accent3)' : score >= 6 ? 'var(--yellow)' : 'var(--red)';
    const actualCol = (r.actual_pnl || 0) >= 0 ? 'var(--accent3)' : 'var(--red)';
    const hypPnl    = r.hypothetical_pnl || 0;
    const hypCol    = hypPnl >= 0 ? 'var(--accent3)' : 'var(--red)';
    const delta     = hypPnl - (r.actual_pnl || 0);
    const deltaCol  = delta >= 0 ? 'var(--accent3)' : 'var(--red)';
    const sign      = v => v >= 0 ? '+' : '';
    const dateStr   = (r.open_time || '').slice(0, 10);
    const dirBadge  = r.direction === 'Long'
      ? '<span class="badge" style="background:rgba(38,217,107,.12);color:var(--accent3);font-size:.65rem">L</span>'
      : '<span class="badge" style="background:rgba(239,83,80,.12);color:var(--red);font-size:.65rem">S</span>';
    const recBadge  = r.would_enter
      ? '<span style="color:var(--accent3);font-size:.75rem;font-weight:600">ENTER</span>'
      : '<span style="color:var(--red);font-size:.75rem;font-weight:600">SKIP</span>';
    const verdBadge = _verdictBadge(r.verdict);
    const sym = (r.symbol || '').replace('USDT', '');

    return `<div class="hindsight-row">
      <div>${dirBadge} <span style="font-weight:600">${sym}</span></div>
      <div style="font-size:.75rem;color:var(--muted)">${dateStr}</div>
      <div style="color:${actualCol};font-weight:600">${sign(r.actual_pnl||0)}${fmtC(r.actual_pnl||0)}</div>
      <div style="color:${scoreCol};font-weight:700">${score}/10</div>
      <div>${recBadge}</div>
      <div style="color:${hypCol}">${hypPnl !== 0 ? sign(hypPnl)+fmtC(hypPnl) : '—'}</div>
      <div style="color:${deltaCol};font-size:.8rem">${delta !== 0 ? sign(delta)+fmtC(delta) : '—'}</div>
      <div>${verdBadge}</div>
    </div>`;
  }).join('');

  return `<div class="hindsight-table">${hdr}${bodyRows}</div>`;
}

function _verdictBadge(v) {
  const map = {
    TP:      ['rgba(38,217,107,.15)',  'var(--accent3)', 'TP'],
    TN:      ['rgba(38,217,107,.10)',  'var(--accent3)', 'TN'],
    FP:      ['rgba(239,83,80,.15)',   'var(--red)',     'FP'],
    FN:      ['rgba(255,179,0,.12)',   'var(--yellow)',  'FN'],
    NEUTRAL: ['rgba(121,134,203,.1)',  'var(--muted)',   '—'],
  };
  const [bg, col, label] = map[v] || map.NEUTRAL;
  return `<span class="badge" style="background:${bg};color:${col};font-size:.68rem">${label}</span>`;
}
