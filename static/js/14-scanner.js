// ══════════════════════════════════════════════════════════════════════════════
// SETUP SCANNER — 9-10/10 high-conviction trade opportunities
// ══════════════════════════════════════════════════════════════════════════════
// Note: innerHTML used with server-sourced data (Claude/Bitget API).
// Pattern is consistent with the rest of the codebase.

let _scanPollInterval = null;
let _scanLastState    = null;

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

// ── Render ────────────────────────────────────────────────────────────────────

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
    statusHtml = `<span style="color:var(--yellow)">⏳ Scanning — ${state.scanned||0} symbols · ${state.after_filter||0} finalists queued for AI…</span>`;
  } else if (done) {
    const ago = state.completed_at ? Math.round((Date.now()/1000 - state.completed_at)/60) : 0;
    const dur = state.duration_sec ? ` in ${state.duration_sec}s` : '';
    const col = (state.setups||[]).length ? 'var(--accent3)' : 'var(--muted)';
    statusHtml = `<span style="color:var(--muted)">Last scan ${ago<1?'just now':ago+'m ago'}${dur} · `
      + `${state.scanned||0} scanned · ${state.after_filter||0} to AI · `
      + `<strong style="color:${col}">${(state.setups||[]).length} setup${(state.setups||[]).length!==1?'s':''} found</strong></span>`;
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
      Watching ${window._scannerWatchlistCount||40} symbols · Results cached 30 min · Scans ~40 symbols across 4H + 1D
    </div>`;
}

function renderScannerResults(state) {
  const el = document.getElementById('scanner-results');
  if (!el) return;

  if (state.status === 'idle') {
    el.innerHTML = `<div class="no-positions"><div class="icon">🔍</div>
      <div style="font-weight:600;margin-bottom:6px">Ready to scan</div>
      <div style="color:var(--muted);font-size:.85rem">
        Scans ${window._scannerWatchlistCount||40} symbols for 9-10/10 setups.<br>
        Three stages: confluence filter → quality gate → AI scoring.
      </div></div>`;
    return;
  }
  if (state.status === 'running') {
    el.innerHTML = `<div class="no-positions">
      <div class="icon" style="animation:pulse 1.5s infinite">⏳</div>
      <div style="font-weight:600;margin-bottom:6px">Scan in progress…</div>
      <div style="color:var(--muted);font-size:.85rem;line-height:1.8">
        Stage 1 — computing multi-TF confluence<br>
        Stage 2 — technical quality gate (RSI, ADX, S/R structure)<br>
        Stage 3 — AI scoring finalists (9-10/10 only returned)
      </div></div>`;
    return;
  }
  if (state.status === 'error') {
    el.innerHTML = `<div style="color:var(--red);padding:24px">Scan error: ${state.error||'unknown'}</div>`;
    return;
  }

  const setups = state.setups || [];
  if (!setups.length) {
    el.innerHTML = `<div class="no-positions">
      <div class="icon">😴</div>
      <div style="font-weight:600;margin-bottom:6px">No 9-10/10 setups right now</div>
      <div style="color:var(--muted);font-size:.85rem">
        Market may be choppy, overextended, or lacking structural entry points.<br>
        Try again after the next candle close or when a key level is tested.
      </div></div>`;
    return;
  }

  el.innerHTML = `
    <div style="font-size:.8rem;color:var(--muted);margin-bottom:16px">
      ${setups.length} high-conviction setup${setups.length!==1?'s':''} — sorted by score
    </div>
    <div class="scanner-grid">${setups.map(renderSetupCard).join('')}</div>`;
}

// ── Setup card ────────────────────────────────────────────────────────────────

function renderSetupCard(s) {
  const sym    = s._symbol || s.symbol || '?';
  const base   = sym.replace('USDT', '');
  const dir    = (s.direction || '').toLowerCase();
  const isLong = dir === 'long';
  const score  = s.setup_score || 0;

  const dirColor = isLong ? 'var(--accent3)' : 'var(--red)';
  const dirBg    = isLong ? 'rgba(38,217,107,.12)' : 'rgba(239,83,80,.12)';
  const border   = isLong ? 'var(--accent3)' : 'var(--red)';

  const ent     = s.entry_zone || {};
  const entLow  = ent.low  ? fmtPrice(ent.low)  : '';
  const entHigh = ent.high ? fmtPrice(ent.high) : '';
  const entText = (entLow && entHigh && ent.low !== ent.high)
    ? `${entLow} – ${entHigh}` : (entLow || entHigh || '—');

  const sl   = s.sl_price  ? fmtPrice(s.sl_price)  : '—';
  const tp1  = s.tp1_price ? fmtPrice(s.tp1_price) : '—';
  const tp2  = s.tp2_price ? fmtPrice(s.tp2_price) : '—';
  const rr   = s.rr_ratio || '—';

  const scoreStar = score >= 10 ? '⭐⭐' : '⭐';
  const urgBadge  = _urgencyBadge(s.urgency);
  const patBadge  = s.chart_pattern
    ? `<span class="badge" style="background:rgba(108,99,255,.15);color:var(--accent);font-size:.7rem">${s.chart_pattern}</span>` : '';

  const conditions = (s.key_conditions || []).slice(0, 4).map(c =>
    `<div style="font-size:.76rem;color:var(--text)">✓ ${c}</div>`).join('');
  const risks = (s.risks || []).map(r =>
    `<div style="font-size:.74rem;color:var(--yellow)">⚠ ${r}</div>`).join('');

  // Pre-fill text for Call Analyzer
  const prefill = [
    `$${base} ${s.direction} setup — Scanner (${score}/10)`,
    ent.low  ? `Entry: $${ent.low}` : '',
    ent.high && ent.high !== ent.low ? `– $${ent.high}` : '',
    s.sl_price  ? `SL: $${s.sl_price}` : '',
    s.tp1_price ? `TP1: $${s.tp1_price}` : '',
    s.tp2_price ? `TP2: $${s.tp2_price}` : '',
    rr !== '—'  ? `R:R ${rr}` : '',
    s.summary || '',
  ].filter(Boolean).join('\n');

  return `
  <div class="scanner-card" style="border-left:3px solid ${border}">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:10px">
      <div>
        <span style="font-size:1.05rem;font-weight:700;color:var(--text)">
          ${base}<span style="color:var(--muted);font-size:.8rem">USDT</span>
        </span>
        <span class="badge" style="background:${dirBg};color:${dirColor};font-size:.75rem;margin-left:6px">
          ${(s.direction||'').toUpperCase()}
        </span>
        ${urgBadge}
        ${patBadge}
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:1.15rem;font-weight:800;color:${dirColor}">${scoreStar} ${score}/10</div>
        <div style="font-size:.7rem;color:var(--muted)">${s.setup_label||''}</div>
      </div>
    </div>

    <div class="scanner-levels">
      <div class="scan-lvl-row">
        <div class="scan-lvl-lbl">Entry zone</div>
        <div class="scan-lvl-val" style="color:var(--accent2)">${entText}</div>
        <div class="scan-lvl-note">${ent.rationale||''}</div>
      </div>
      <div class="scan-lvl-row">
        <div class="scan-lvl-lbl">Stop loss</div>
        <div class="scan-lvl-val" style="color:var(--red)">${sl}</div>
        <div class="scan-lvl-note">${s.sl_rationale||''}</div>
      </div>
      <div class="scan-lvl-row">
        <div class="scan-lvl-lbl">TP1 / TP2</div>
        <div class="scan-lvl-val" style="color:var(--accent3)">${tp1} / ${tp2}</div>
        <div class="scan-lvl-note">R:R ${rr}</div>
      </div>
    </div>

    ${conditions ? `<div style="margin:10px 0 4px;display:flex;flex-direction:column;gap:2px">${conditions}</div>` : ''}
    <div style="font-size:.78rem;color:var(--muted);line-height:1.5;margin:8px 0">${s.summary||''}</div>
    ${risks ? `<div style="display:flex;flex-direction:column;gap:2px;margin-bottom:8px">${risks}</div>` : ''}
    <div style="font-size:.68rem;color:var(--border);margin-bottom:8px">
      ${s._input_tokens||0} in / ${s._output_tokens||0} out tokens
    </div>

    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-secondary btn-sm" onclick="openChart('${sym}','4H')">📊 Chart</button>
      <button class="btn btn-secondary btn-sm"
        onclick="_sendToCallAnalyzer(${JSON.stringify(prefill)})">📋 Analyze</button>
    </div>
  </div>`;
}

function fmtPrice(v) {
  const n = parseFloat(v);
  if (!n || isNaN(n)) return '—';
  if (n >= 10000) return n.toLocaleString('en-US', {maximumFractionDigits: 0});
  if (n >= 1000)  return n.toLocaleString('en-US', {maximumFractionDigits: 1});
  if (n >= 1)     return n.toFixed(4);
  return n.toPrecision(4);
}

function _urgencyBadge(urgency) {
  if (!urgency) return '';
  const map = {
    'Now':      ['rgba(239,83,80,.2)',   'var(--red)'],
    '1-4h':     ['rgba(255,179,0,.15)',  'var(--yellow)'],
    'Today':    ['rgba(108,99,255,.15)', 'var(--accent)'],
    '1-3 days': ['rgba(121,134,203,.1)', 'var(--muted)'],
  };
  const [bg, col] = map[urgency] || map['1-3 days'];
  return `<span class="badge" style="background:${bg};color:${col};font-size:.68rem">${urgency}</span>`;
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
  if (res.ok) window._scannerWatchlistCount = (res.data.symbols || []).length;
}
