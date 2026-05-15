
async function loadAccuracyProgress() {
  const res = await api('/api/calls/accuracy-progress');
  if (!res.ok) return;
  const d = res.data;

  const sec = document.getElementById('prediction-accuracy-section');
  if (sec) sec.style.display = '';

  let card = document.getElementById('accuracy-progress-card');
  if (!card) {
    card = document.createElement('div');
    card.id = 'accuracy-progress-card';
    card.style.cssText = 'margin-bottom:16px;padding:14px 18px;background:var(--bg2);border:1px solid var(--border);border-radius:10px';
    const content = document.getElementById('prediction-accuracy-content');
    if (content) content.parentElement.insertBefore(card, content);
    else return;
  }

  card.textContent = '';

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:10px';

  const title = document.createElement('span');
  title.style.cssText = 'font-size:.85rem;font-weight:600';
  title.textContent = '\u{1F4CA} Accuracy Tracking';

  const rateColor = d.win_rate >= 55 ? 'var(--accent3)' : d.win_rate >= 40 ? 'var(--yellow)' : 'var(--red)';
  const meta = document.createElement('span');
  meta.style.cssText = 'font-size:.8rem;color:var(--muted)';
  meta.textContent = d.recorded + ' / ' + d.target + ' calls recorded · ';
  const rateSpan = document.createElement('span');
  rateSpan.style.color = rateColor;
  rateSpan.textContent = d.win_rate + '% win rate';
  meta.appendChild(rateSpan);

  header.appendChild(title);
  header.appendChild(meta);

  const barWrap = document.createElement('div');
  barWrap.style.cssText = 'background:var(--bg);border-radius:6px;height:8px;overflow:hidden;margin-bottom:8px';
  const fill = document.createElement('div');
  const pct  = Math.min(100, Math.round(d.recorded / d.target * 100));
  fill.style.cssText = 'height:100%;border-radius:6px;transition:width .4s;width:' + pct + '%;background:' +
    (d.enough_data ? 'var(--accent3)' : 'var(--accent)');
  barWrap.appendChild(fill);

  const note = document.createElement('div');
  note.style.cssText = 'font-size:.75rem;color:var(--muted)';
  note.textContent = d.enough_data
    ? '✅ Statistical target reached — accuracy data is reliable'
    : d.remaining + ' more outcome-recorded calls needed for statistical confidence';

  card.appendChild(header);
  card.appendChild(barWrap);
  card.appendChild(note);

  // Add backtest card below accuracy card if not already present
  if (!document.getElementById('backtest-card')) {
    const btCard = document.createElement('div');
    btCard.id = 'backtest-card';
    btCard.style.cssText = 'margin-top:12px;padding:14px 18px;background:var(--bg2);border:1px solid var(--border);border-radius:10px';

    const btTitle = document.createElement('div');
    btTitle.style.cssText = 'font-size:.85rem;font-weight:600;margin-bottom:10px';
    btTitle.textContent = 'Backtest (6M · 4H)';

    const inputRow = document.createElement('div');
    inputRow.style.cssText = 'display:flex;gap:8px;margin-bottom:8px';

    const symbolInput = document.createElement('input');
    symbolInput.id = 'backtestSymbol';
    symbolInput.type = 'text';
    symbolInput.placeholder = 'BTCUSDT';
    symbolInput.value = 'BTCUSDT';
    symbolInput.style.cssText = 'flex:1;padding:4px 8px;font-size:.8rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)';

    const runBtn = document.createElement('button');
    runBtn.id = 'backtestRunBtn';
    runBtn.style.cssText = 'padding:4px 12px;font-size:.8rem;background:var(--accent);border:none;border-radius:6px;color:#fff;cursor:pointer';
    runBtn.textContent = '► Run';
    runBtn.onclick = function() { loadBacktest(); };

    const optBtn = document.createElement('button');
    optBtn.id = 'backtestOptBtn';
    optBtn.style.cssText = 'padding:4px 12px;font-size:.8rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer';
    optBtn.textContent = '⚙ Optimize';
    optBtn.title = 'Bayesian parameter search (~5-10 min)';
    optBtn.onclick = function() { loadOptimizer(); };

    inputRow.appendChild(symbolInput);
    inputRow.appendChild(runBtn);
    inputRow.appendChild(optBtn);

    const resultDiv = document.createElement('div');
    resultDiv.id = 'backtestResult';
    resultDiv.style.cssText = 'font-size:.8rem;color:var(--muted)';
    resultDiv.textContent = 'Enter symbol and click Run';

    const optimizerDiv = document.createElement('div');
    optimizerDiv.id = 'optimizerResult';
    optimizerDiv.style.cssText = 'font-size:.8rem;margin-top:8px';

    btCard.appendChild(btTitle);
    btCard.appendChild(inputRow);
    btCard.appendChild(resultDiv);
    btCard.appendChild(optimizerDiv);
    card.parentElement.insertBefore(btCard, card.nextSibling);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// BACKTEST
// ══════════════════════════════════════════════════════════════════════════════

function _renderBacktestResult(container, d) {
  container.textContent = '';
  const metrics = [
    ['Trades',  String(d.total_trades)],
    ['Win %',   d.win_rate + '%'],
    ['PF',      String(d.profit_factor)],
    ['Sharpe',  String(d.sharpe)],
    ['Max DD',  (d.max_drawdown * 100).toFixed(1) + '%'],
  ];
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap;margin-top:8px';
  for (const [label, val] of metrics) {
    const cell = document.createElement('div');
    cell.style.cssText = 'display:flex;flex-direction:column;align-items:center;min-width:60px';
    const valEl = document.createElement('div');
    valEl.style.cssText = 'font-size:.9rem;font-weight:600';
    valEl.textContent = val;
    const labelEl = document.createElement('div');
    labelEl.style.cssText = 'font-size:.7rem;color:var(--muted)';
    labelEl.textContent = label;
    cell.appendChild(valEl);
    cell.appendChild(labelEl);
    row.appendChild(cell);
  }
  container.appendChild(row);
}

async function loadBacktest(symbol) {
  const sym = symbol
    || (document.getElementById('backtestSymbol') || {}).value?.trim()
    || 'BTCUSDT';
  const container = document.getElementById('backtestResult');
  if (!container) return;
  container.textContent = 'Running backtest…';

  try {
    const json = await api('/api/backtest/run', 'POST', { symbol: sym, timeframe: '4H', days: 180 });
    if (!json.ok) throw new Error(json.error || 'Backtest failed');
    _renderBacktestResult(container, json.data);
  } catch (e) {
    container.textContent = '';
    const err = document.createElement('small');
    err.style.color = 'var(--red)';
    err.textContent = e.message;
    container.appendChild(err);
    notify('Backtest error: ' + e.message, 'danger');
  }
}

function _setBtBtnsDisabled(disabled) {
  const r = document.getElementById('backtestRunBtn');
  const o = document.getElementById('backtestOptBtn');
  if (r) r.disabled = disabled;
  if (o) o.disabled = disabled;
}

async function loadOptimizer() {
  const sym = (document.getElementById('backtestSymbol') || {}).value?.trim() || 'BTCUSDT';
  const container = document.getElementById('optimizerResult');
  if (!container) return;

  _setBtBtnsDisabled(true);
  container.textContent = '';
  const msg = document.createElement('small');
  msg.style.color = 'var(--muted)';
  msg.textContent = '⧗ Starting optimizer for ' + sym + '…';
  container.appendChild(msg);

  try {
    const startRes = await api('/api/backtest/optimize?symbol=' + encodeURIComponent(sym) + '&n_trials=50');
    if (!startRes.ok) throw new Error(startRes.error || 'Failed to start optimizer');

    const jobId = startRes.data.job_id;
    msg.textContent = '⧗ Optimizer running for ' + sym + ' (~5-10 min)… polling every 10s';

    const pollInterval = setInterval(async () => {
      try {
        const pollRes = await api('/api/backtest/optimize/' + jobId);
        if (!pollRes.ok) {
          clearInterval(pollInterval);
          _setBtBtnsDisabled(false);
          container.textContent = '';
          const err = document.createElement('small');
          err.style.color = 'var(--red)';
          err.textContent = 'Optimizer error: ' + (pollRes.error || 'unknown');
          container.appendChild(err);
          return;
        }
        if (pollRes.data.status === 'complete') {
          clearInterval(pollInterval);
          _setBtBtnsDisabled(false);
          _renderOptimizerResult(container, pollRes.data.result, sym);
          notify('Optimizer complete for ' + sym, 'success');
        }
        // status === 'running': keep polling
      } catch (pollErr) {
        clearInterval(pollInterval);
        _setBtBtnsDisabled(false);
        container.textContent = 'Poll error: ' + pollErr.message;
      }
    }, 10000);

  } catch (e) {
    container.textContent = '';
    const err = document.createElement('small');
    err.style.color = 'var(--red)';
    err.textContent = e.message;
    container.appendChild(err);
    _setBtBtnsDisabled(false);
    notify('Optimizer error: ' + e.message, 'danger');
  }
}

function _renderOptimizerResult(container, params, sym) {
  container.textContent = '';
  const title = document.createElement('div');
  title.style.cssText = 'font-size:.75rem;font-weight:600;color:var(--muted);margin-bottom:6px';
  title.textContent = 'Best params (' + sym + ')';
  container.appendChild(title);
  const paramLabels = {
    wt_oversold: 'WT oversold', rsi_max: 'RSI max', adx_min: 'ADX min',
    min_confluence: 'Confluence', sl_pct: 'SL %', tp1_pct: 'TP1 %', tp2_pct: 'TP2 %',
  };
  const grid = document.createElement('div');
  grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
  for (const [key, label] of Object.entries(paramLabels)) {
    if (!(key in (params || {}))) continue;
    const val = typeof params[key] === 'number' ? params[key].toFixed(2) : String(params[key]);
    const chip = document.createElement('div');
    chip.style.cssText = 'padding:3px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:.75rem';
    const k = document.createElement('span');
    k.style.color = 'var(--muted)';
    k.textContent = label + ': ';
    const v = document.createElement('span');
    v.style.fontWeight = '600';
    v.textContent = val;
    chip.appendChild(k);
    chip.appendChild(v);
    grid.appendChild(chip);
  }
  container.appendChild(grid);
}

// ══════════════════════════════════════════════════════════════════════════════
// PREDICTION ACCURACY
// ══════════════════════════════════════════════════════════════════════════════
async function loadPredictionAccuracy() {
  loadAccuracyProgress();
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
