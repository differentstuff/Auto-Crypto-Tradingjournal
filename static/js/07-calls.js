
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
      ${d.pattern_flags.map(f => `<div style="margin-bottom:4px;color:var(--text)">• ${mdToHtml(f)}</div>`).join('')}
    </div>`;
  }

  // Chart analysis (if available)
  if (d.chart_analysis) {
    html += `<div class="ai-overall" style="margin-bottom:16px;line-height:1.6">${mdToHtml(d.chart_analysis)}</div>`;
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
    html += `<div class="warn-box"><strong>⚠ Candle-Close Stop Loss</strong><div style="margin-top:4px">${mdToHtml(d.sl_warning)}</div></div>`;
  }

  // Entry timing
  if (d.entry_timing) {
    html += `<div style="margin:12px 0;font-size:.83rem">
      <span style="color:var(--muted);text-transform:uppercase;font-size:.72rem;font-weight:700">Entry Timing</span><br>
      <span style="margin-top:4px;display:block;line-height:1.6">${mdToHtml(d.entry_timing)}</span>
    </div>`;
  }

  // Summary
  if (d.summary) {
    html += `<div class="ai-overall" style="margin:14px 0;line-height:1.6">${mdToHtml(d.summary)}</div>`;
  }

  // Optimizations
  if (d.optimizations?.length) {
    html += `<div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--muted);margin:14px 0 6px">Optimizations</div>
    <ul class="call-list">${d.optimizations.map(o => `<li><span>💡</span>${mdToHtml(o)}</li>`).join('')}</ul>`;
  }

  // Risks
  if (d.risks?.length) {
    html += `<div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--muted);margin:14px 0 6px">Risks</div>
    <ul class="call-list">${d.risks.map(r => `<li><span>⚠</span>${mdToHtml(r)}</li>`).join('')}</ul>`;
  }

  // Historical context
  if (d.historical_context) {
    html += `<div class="call-history-note">📊 ${mdToHtml(d.historical_context)}</div>`;
  }

  // Chain-of-thought reasoning (collapsible)
  if (d.cot_reasoning || d.thinking) {
    const cot = d.cot_reasoning || d.thinking;
    html += `<details style="margin-top:14px;border:1px solid var(--border);border-radius:8px;overflow:hidden">
      <summary style="padding:8px 12px;cursor:pointer;font-size:.75rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;list-style:none;display:flex;align-items:center;gap:6px;user-select:none">
        <span>▶</span> 🧠 Claude's Reasoning
      </summary>
      <div style="padding:10px 14px;font-size:.8rem;line-height:1.65;color:var(--text);border-top:1px solid var(--border);background:rgba(108,99,255,.03)">
        ${mdToHtml(cot)}
      </div>
    </details>`;
  }

  // Token usage
  html += `<div style="font-size:.7rem;color:var(--border);text-align:right;margin-top:12px">
    ${d._input_tokens} in / ${d._output_tokens} out tokens · claude-sonnet-4-6</div>`;

  result.innerHTML = html;

  // Append Save + Chart buttons after result renders
  const chartBtn = d.symbol
    ? `<button class="btn-chart-sm" onclick="openChart('${d.symbol}')">📊 Chart</button>`
    : '';
  result.insertAdjacentHTML('beforeend', `
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button class="btn btn-primary" id="call-save-btn" onclick="saveCurrentCall()">💾 Save This Call</button>
      ${chartBtn}
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

function toggleCallLegend() {
  const panel = document.getElementById('call-legend');
  const btn   = document.getElementById('btn-call-legend');
  if (!panel || !btn) return;
  const open = panel.style.display !== 'none';
  panel.style.display = open ? 'none' : '';
  btn.textContent = open ? 'ℹ How to read the results' : '✕ Close legend';
}
