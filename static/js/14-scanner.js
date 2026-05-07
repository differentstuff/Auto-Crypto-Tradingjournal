// ══════════════════════════════════════════════════════════════════════════════
// SETUP SCANNER — rated setups 6-10/10, table view with click-to-expand detail
// ══════════════════════════════════════════════════════════════════════════════

let _scanPollInterval  = null;
let _scanLastState     = null;
let _scanExpandedIdx   = null;
let _scanSetups        = [];

async function loadScanner() {
  _loadScannerWatchlist();
  const state = await api('/api/scanner/status');
  if (!state.ok) return;
  renderScannerPage(state.data);
  if (state.data.status === 'running') _startScanPoller();
}

async function startScan(force) {
  const btn = document.getElementById('btn-scan');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Starting…'; }
  const url = '/api/scanner/run' + (force ? '?force=1' : '');
  const res = await api(url, 'POST');
  if (!res.ok) {
    if (btn) { btn.disabled = false; btn.textContent = '🔍 Scan Now'; }
    return;
  }
  renderScannerPage(res.data);
  if (res.data.status === 'running') _startScanPoller();
}

function _startScanPoller() {
  _stopScanPoller();
  _scanPollInterval = setInterval(async () => {
    const res = await api('/api/scanner/status');
    if (!res.ok) return;
    renderScannerPage(res.data);
    if (res.data.status !== 'running') _stopScanPoller();
  }, 2500);
}
function _stopScanPoller() {
  if (_scanPollInterval) { clearInterval(_scanPollInterval); _scanPollInterval = null; }
}

// ── Page render ───────────────────────────────────────────────────────────────

function renderScannerPage(state) {
  _scanLastState = state;
  renderScannerMeta(state);
  renderScannerResults(state);
}

function renderScannerMeta(state) {
  const el = document.getElementById('scanner-meta');
  if (!el) return;
  const running = state.status === 'running';
  const done    = state.status === 'completed';

  let statusHtml = '';
  if (running) {
    statusHtml = `<span style="color:var(--yellow)">⏳ Scanning — ${state.scanned||0} symbols · ${state.after_filter||0} sent to AI…</span>`;
  } else if (done) {
    const ago = state.completed_at ? Math.round((Date.now()/1000 - state.completed_at)/60) : 0;
    const dur = state.duration_sec ? ` in ${state.duration_sec}s` : '';
    const n   = (state.setups||[]).length;
    const col = n ? 'var(--accent3)' : 'var(--muted)';
    statusHtml = `<span style="color:var(--muted)">Last scan ${ago<1?'just now':ago+'m ago'}${dur} · `
      + `${state.scanned||0} symbols · <strong style="color:${col}">${n} setup${n!==1?'s':''} found</strong></span>`;
  } else if (state.status === 'error') {
    statusHtml = `<span style="color:var(--red)">Error: ${state.error||'unknown'}</span>`;
  } else {
    statusHtml = `<span style="color:var(--muted)">No scan run yet.</span>`;
  }

  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <button class="btn btn-primary" id="btn-scan" onclick="startScan(false)" ${running?'disabled':''}>
        ${running ? '⏳ Scanning…' : '🔍 Scan Now'}
      </button>
      ${done ? '<button class="btn btn-secondary btn-sm" onclick="startScan(true)">🔄 Re-scan</button>' : ''}
      <span style="font-size:.82rem">${statusHtml}</span>
    </div>
    <div style="font-size:.78rem;color:var(--muted);margin-top:6px">
      ${window._scannerWatchlistCount||40} symbols · scores 6-10 · results cached 30 min · click a row for entry/SL/TP details
    </div>`;
}

function renderScannerResults(state) {
  const el = document.getElementById('scanner-results');
  if (!el) return;

  if (state.status === 'idle') {
    el.innerHTML = `<div class="no-positions"><div class="icon">🔍</div>
      <div style="font-weight:600;margin-bottom:6px">Ready to scan</div>
      <div style="color:var(--muted);font-size:.85rem">
        Scans ${window._scannerWatchlistCount||40} symbols and scores setups 6-10/10.<br>
        Click a result row to see entry zone, stop loss, and take-profit with full rationale.
      </div></div>`;
    return;
  }
  if (state.status === 'running') {
    el.innerHTML = `<div class="no-positions">
      <div class="icon" style="animation:pulse 1.5s infinite">⏳</div>
      <div style="font-weight:600;margin-bottom:6px">Scan in progress…</div>
      <div style="color:var(--muted);font-size:.85rem;line-height:1.8">
        Stage 1 — multi-TF confluence (parallel)<br>
        Stage 2 — technical quality gate<br>
        Stage 3 — AI scoring (returns 6-10/10 only)
      </div></div>`;
    return;
  }
  if (state.status === 'error') {
    el.innerHTML = `<div style="color:var(--red);padding:24px">Scan error: ${state.error||'unknown'}</div>`;
    return;
  }

  _scanSetups = state.setups || [];
  _scanExpandedIdx = null;

  if (!_scanSetups.length) {
    el.innerHTML = `<div class="no-positions">
      <div class="icon">😴</div>
      <div style="font-weight:600;margin-bottom:6px">No setups found (6+/10)</div>
      <div style="color:var(--muted);font-size:.85rem">
        No symbols passed all three stages right now.<br>
        Market may be choppy or overextended — try again after a key level test.
      </div></div>`;
    return;
  }

  el.innerHTML = `
    <div style="font-size:.8rem;color:var(--muted);margin-bottom:12px">
      ${_scanSetups.length} setup${_scanSetups.length!==1?'s':''} · sorted by score · click a row for details
    </div>
    ${buildScannerTable(_scanSetups)}`;
}

// ── Table ─────────────────────────────────────────────────────────────────────

function buildScannerTable(setups) {
  const rows = setups.map((s, i) => buildScannerRow(s, i)).join('');
  return `<div class="scanner-tbl-wrap">
    <table class="scanner-tbl">
      <thead><tr>
        <th>Score</th><th>Symbol</th><th>Dir</th>
        <th>Confluence</th><th>Pattern</th>
        <th>Entry Zone</th><th>R:R</th><th>Urgency</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

function buildScannerRow(s, i) {
  const sym    = s._symbol || s.symbol || '?';
  const base   = sym.replace('USDT','');
  const dir    = (s.direction||'').toLowerCase();
  const isLong = dir === 'long';
  const score  = s.setup_score || 0;

  const scoreCol = score >= 9 ? 'var(--accent3)' : score >= 7 ? 'var(--yellow)' : 'var(--muted)';
  const dirColor = isLong ? 'var(--accent3)' : 'var(--red)';
  const dirBg    = isLong ? 'rgba(38,217,107,.12)' : 'rgba(239,83,80,.12)';

  const ent  = s.entry_zone || {};
  const entL = ent.low  ? fmtSP(ent.low)  : '';
  const entH = ent.high ? fmtSP(ent.high) : '';
  const entTxt = (entL && entH && ent.low !== ent.high) ? `${entL}–${entH}` : entL || '—';

  const conf    = (s.confluence_summary || s.key_conditions?.[0] || '').slice(0, 40);
  const pattern = s.chart_pattern || '—';
  const urgBg   = {Now:'rgba(239,83,80,.15)','1-4h':'rgba(255,179,0,.12)',
                   Today:'rgba(108,99,255,.12)','1-3 days':'rgba(121,134,203,.08)'}[s.urgency] || '';
  const urgCol  = {Now:'var(--red)','1-4h':'var(--yellow)',Today:'var(--accent)','1-3 days':'var(--muted)'}[s.urgency] || 'var(--muted)';

  const row = `<tr class="scanner-row" onclick="toggleScanDetail(${i})" data-idx="${i}">
    <td><span style="font-size:1rem;font-weight:800;color:${scoreCol}">${score}</span>
        <span style="font-size:.7rem;color:var(--muted)">/10</span></td>
    <td style="font-weight:700">${base}<span style="color:var(--muted);font-size:.7rem">USDT</span></td>
    <td><span class="badge" style="background:${dirBg};color:${dirColor};font-size:.72rem">${(s.direction||'').toUpperCase()}</span></td>
    <td style="font-size:.75rem;color:var(--muted);max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${conf}</td>
    <td style="font-size:.75rem;color:var(--accent)">${pattern}</td>
    <td style="font-size:.78rem;color:var(--accent2)">${entTxt}</td>
    <td style="font-size:.82rem;font-weight:600;color:var(--text)">${s.rr_ratio||'—'}</td>
    <td><span class="badge" style="background:${urgBg};color:${urgCol};font-size:.68rem">${s.urgency||'—'}</span></td>
  </tr>
  <tr id="scan-detail-${i}" class="scanner-detail-row" style="display:none">
    <td colspan="8">${buildDetailPanel(s, i)}</td>
  </tr>`;
  return row;
}

function buildDetailPanel(s, i) {
  const sym  = s._symbol || s.symbol || '?';
  const ent  = s.entry_zone || {};
  const isLong = (s.direction||'').toLowerCase() === 'long';
  const scoreLabel = s.setup_label || '';

  function lvl(title, price, rationale, color) {
    if (!price) return '';
    return `<div class="scan-dp-level">
      <div class="scan-dp-lbl" style="color:${color}">${title}</div>
      <div class="scan-dp-price" style="color:${color}">${fmtSP(price)}</div>
      <div class="scan-dp-rat">${rationale||''}</div>
    </div>`;
  }

  const entryColor = 'var(--accent2)';
  const entText = (ent.low && ent.high && ent.low !== ent.high)
    ? `${fmtSP(ent.low)} – ${fmtSP(ent.high)}` : fmtSP(ent.low || ent.high);

  const conditions = (s.key_conditions||[]).map(c => `<span class="scan-dp-cond">✓ ${c}</span>`).join('');
  const risks      = (s.risks||[]).map(r => `<span class="scan-dp-risk">⚠ ${r}</span>`).join('');

  const prefill = [
    `$${sym.replace('USDT','')} ${s.direction} — Scanner ${s.setup_score}/10`,
    ent.low  ? `Entry: $${ent.low}` : '',
    ent.high && ent.high !== ent.low ? `– $${ent.high}` : '',
    s.sl_price  ? `SL: $${s.sl_price}`  : '',
    s.tp1_price ? `TP1: $${s.tp1_price}` : '',
    s.tp2_price ? `TP2: $${s.tp2_price}` : '',
    s.rr_ratio ? `R:R ${s.rr_ratio}` : '',
    s.summary || '',
  ].filter(Boolean).join('\n');

  return `<div class="scan-detail-panel">
    <div class="scan-dp-header">
      <span style="font-weight:700;font-size:.95rem">${sym.replace('USDT','')}USDT
        <span style="color:${isLong?'var(--accent3)':'var(--red)';}">${s.direction}</span> —
        <span style="color:${s.setup_score>=9?'var(--accent3)':s.setup_score>=7?'var(--yellow)':'var(--muted)'}">${s.setup_score}/10 ${scoreLabel}</span>
      </span>
    </div>

    ${s.summary ? `<div class="scan-dp-summary">${s.summary}</div>` : ''}

    <div class="scan-dp-levels">
      <div class="scan-dp-level">
        <div class="scan-dp-lbl" style="color:${entryColor}">ENTRY ZONE</div>
        <div class="scan-dp-price" style="color:${entryColor}">${entText}</div>
        <div class="scan-dp-rat">${ent.rationale||''}</div>
      </div>
      ${lvl('STOP LOSS', s.sl_price, s.sl_rationale, 'var(--red)')}
      ${lvl('TAKE PROFIT 1', s.tp1_price, s.tp1_rationale, 'var(--accent3)')}
      ${lvl('TAKE PROFIT 2', s.tp2_price, s.tp2_rationale, 'var(--accent3)')}
    </div>

    ${conditions ? `<div class="scan-dp-conds">${conditions}</div>` : ''}
    ${risks      ? `<div class="scan-dp-risks">${risks}</div>`      : ''}

    <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
      <button class="btn btn-secondary btn-sm" onclick="openChart('${sym}','4H');event.stopPropagation()">📊 Chart</button>
      <button class="btn btn-secondary btn-sm"
        onclick="_sendToCallAnalyzer(${JSON.stringify(prefill)});event.stopPropagation()">📋 Analyze</button>
      <span style="font-size:.68rem;color:var(--border);align-self:center">
        ${s._input_tokens||0} in / ${s._output_tokens||0} out tokens
      </span>
    </div>
  </div>`;
}

function toggleScanDetail(i) {
  const row = document.getElementById('scan-detail-' + i);
  if (!row) return;
  const isOpen = row.style.display !== 'none';

  // Close any open panel
  if (_scanExpandedIdx !== null) {
    const prev = document.getElementById('scan-detail-' + _scanExpandedIdx);
    if (prev) prev.style.display = 'none';
    document.querySelectorAll('.scanner-row').forEach(r => r.classList.remove('active'));
    _scanExpandedIdx = null;
  }

  if (!isOpen) {
    row.style.display = '';
    const mainRow = document.querySelector(`.scanner-row[data-idx="${i}"]`);
    if (mainRow) mainRow.classList.add('active');
    _scanExpandedIdx = i;
    // Scroll into view if needed
    row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtSP(v) {
  const n = parseFloat(v);
  if (!n || isNaN(n)) return '—';
  if (n >= 10000) return n.toLocaleString('en-US', {maximumFractionDigits: 0});
  if (n >= 1000)  return n.toLocaleString('en-US', {maximumFractionDigits: 1});
  if (n >= 1)     return n.toFixed(4);
  return n.toPrecision(4);
}

function _sendToCallAnalyzer(text) {
  showPage('calls');
  setTimeout(() => {
    const ta = document.getElementById('call-text');
    if (ta) { ta.value = text; ta.focus(); }
  }, 150);
}

async function _loadScannerWatchlist() {
  const res = await api('/api/scanner/watchlist');
  if (res.ok) window._scannerWatchlistCount = (res.data.symbols||[]).length;
}
