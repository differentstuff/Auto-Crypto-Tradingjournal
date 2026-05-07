
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
