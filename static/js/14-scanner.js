// ══════════════════════════════════════════════════════════════════════════════
// SETUP SCANNER — rated setups 6-10/10, live progress, custom criteria
// ══════════════════════════════════════════════════════════════════════════════

let _scanPollInterval  = null;
let _scanLastState     = null;
let _scanExpandedIdx   = null;
let _scanSetups        = [];

// ── Criteria definition ───────────────────────────────────────────────────────

const SCAN_CRITERIA = [
  { key: 'rsi',        label: 'RSI filter',            desc: 'Reject/penalise overextended RSI (>78 long, <22 short) in quality gate and Claude prompt' },
  { key: 'macd',       label: 'MACD alignment',        desc: 'Count MACD crossover/direction as a 4H signal and factor into Claude scoring' },
  { key: 'ema_stack',  label: 'EMA stack',             desc: 'Count EMA alignment (20>50>200 bullish or reverse) as a 4H signal' },
  { key: 'adx',        label: 'ADX ≥ 15 trend',        desc: 'Reject flat/choppy markets with ADX below 15 in quality gate' },
  { key: 'sr_anchor',  label: 'S/R structural anchor', desc: 'Require entry within 4×ATR of a named S/R level — disable for memes or news moves' },
  { key: 'wavetrend',  label: 'WaveTrend (VMC)',        desc: 'Include VMC Cipher A/B WaveTrend signal in Claude scoring' },
  { key: 'volume',     label: 'Volume confirm',        desc: 'Reward volume confirmation of the setup move in Claude scoring' },
  { key: 'funding',    label: 'Funding rate',          desc: 'Penalise score −1/−2 when funding rate is crowded (>0.05% / >0.1%)' },
  { key: 'fear_greed', label: 'Fear & Greed',          desc: 'Apply ±0.5 score adjustment for extreme Fear & Greed readings' },
  { key: 'atr_sl',     label: 'ATR SL floor',          desc: 'Cap score ≤ 6 when SL is tighter than 1×ATR from entry (inside noise)' },
  { key: 'rr_minimum', label: 'R:R minimum',           desc: 'Cap score ≤ 6 for R:R < 1.5:1; require ≥ 2:1 for score 7+' },
];

const SCAN_PRESETS = {
  full:      { label: 'Full (default)',   keys: null },
  trend:     { label: 'Trend Momentum',  keys: { rsi:true,  macd:true,  ema_stack:true,  adx:true,  sr_anchor:false, wavetrend:true,  volume:true,  funding:false, fear_greed:false, atr_sl:false, rr_minimum:true  } },
  structure: { label: 'Structure-only',  keys: { rsi:false, macd:false, ema_stack:false, adx:false, sr_anchor:true,  wavetrend:false, volume:false, funding:true,  fear_greed:false, atr_sl:true,  rr_minimum:true  } },
  meme:      { label: 'Meme / Low-cap',  keys: { rsi:true,  macd:false, ema_stack:false, adx:false, sr_anchor:false, wavetrend:false, volume:true,  funding:true,  fear_greed:true,  atr_sl:false, rr_minimum:false } },
};

const _CR_KEY = 'scanCriteria';

function _loadCriteria() {
  try { const s = localStorage.getItem(_CR_KEY); if (s) return JSON.parse(s); } catch(e) {}
  return null;
}
function _saveCriteria(cr) { localStorage.setItem(_CR_KEY, JSON.stringify(cr)); }

function _readCriteriaFromCheckboxes() {
  const cr = {};
  SCAN_CRITERIA.forEach(c => {
    const el = document.getElementById('cr-' + c.key);
    cr[c.key] = el ? el.checked : true;
  });
  return cr;
}

function _applyPreset(presetKey) {
  const p = SCAN_PRESETS[presetKey];
  if (!p) return;
  SCAN_CRITERIA.forEach(c => {
    const el = document.getElementById('cr-' + c.key);
    if (el) el.checked = (p.keys === null) ? true : (p.keys[c.key] !== false);
  });
  _onCriteriaChange();
}

function _onCriteriaChange() {
  const cr = _readCriteriaFromCheckboxes();
  _saveCriteria(cr);
  // Update preset label
  const pl = document.getElementById('cr-preset-label');
  if (pl) {
    let matched = 'Custom';
    for (const [k, p] of Object.entries(SCAN_PRESETS)) {
      if (p.keys === null) {
        if (SCAN_CRITERIA.every(c => cr[c.key] !== false)) { matched = p.label; break; }
      } else {
        if (SCAN_CRITERIA.every(c => (cr[c.key] !== false) === (p.keys[c.key] !== false))) { matched = p.label; break; }
      }
    }
    pl.textContent = matched;
  }
  // Update count badge
  const cnt = document.getElementById('cr-active-count');
  if (cnt) {
    const n = SCAN_CRITERIA.filter(c => cr[c.key] !== false).length;
    cnt.textContent = n + ' / ' + SCAN_CRITERIA.length + ' criteria active';
    cnt.style.color = n === SCAN_CRITERIA.length ? 'var(--muted)' : 'var(--yellow)';
  }
}

// ── Criteria panel builder (DOM-safe) ─────────────────────────────────────────

function _renderCriteriaPanel() {
  const old = document.getElementById('criteria-panel');
  if (old) old.remove();

  const saved = _loadCriteria() || {};
  const panel = document.createElement('div');
  panel.id = 'criteria-panel';
  panel.className = 'criteria-panel';
  panel.style.display = 'none';

  // Header
  const hdr = document.createElement('div');
  hdr.className = 'criteria-header';
  const hdrText = document.createElement('div');
  const hdrTitle = document.createElement('div');
  hdrTitle.style.cssText = 'font-weight:700;font-size:.9rem;color:var(--text)';
  hdrTitle.textContent = 'Scoring Criteria';
  const hdrSub = document.createElement('div');
  hdrSub.style.cssText = 'font-size:.75rem;color:var(--muted);margin-top:2px';
  hdrSub.textContent = 'Choose which checks Claude applies. Hover any item for details. Disabled criteria are skipped in the quality gate and Claude prompt.';
  hdrText.appendChild(hdrTitle);
  hdrText.appendChild(hdrSub);
  const closeBtn = document.createElement('button');
  closeBtn.className = 'cr-close';
  closeBtn.textContent = '✕';
  closeBtn.onclick = _toggleCriteriaPanel;
  hdr.appendChild(hdrText);
  hdr.appendChild(closeBtn);
  panel.appendChild(hdr);

  // Presets row
  const presetsRow = document.createElement('div');
  presetsRow.className = 'cr-presets';
  const presetsLbl = document.createElement('span');
  presetsLbl.style.cssText = 'font-size:.72rem;color:var(--muted);margin-right:6px';
  presetsLbl.textContent = 'Presets:';
  presetsRow.appendChild(presetsLbl);
  Object.entries(SCAN_PRESETS).forEach(([k, p]) => {
    const btn = document.createElement('button');
    btn.className = 'cr-preset-btn';
    btn.textContent = p.label;
    btn.onclick = () => _applyPreset(k);
    presetsRow.appendChild(btn);
  });
  const presetLabel = document.createElement('span');
  presetLabel.id = 'cr-preset-label';
  presetLabel.style.cssText = 'font-size:.72rem;color:var(--accent2);margin-left:8px';
  presetsRow.appendChild(presetLabel);
  panel.appendChild(presetsRow);

  // Criteria grid
  const grid = document.createElement('div');
  grid.className = 'cr-grid';
  SCAN_CRITERIA.forEach(c => {
    const label = document.createElement('label');
    label.className = 'cr-item';
    label.title = c.desc;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = 'cr-' + c.key;
    cb.checked = (saved[c.key] !== false);
    cb.addEventListener('change', _onCriteriaChange);
    const lbl = document.createElement('span');
    lbl.className = 'cr-label';
    lbl.textContent = c.label;
    const desc = document.createElement('span');
    desc.className = 'cr-desc';
    desc.textContent = c.desc;
    label.appendChild(cb);
    label.appendChild(lbl);
    label.appendChild(desc);
    grid.appendChild(label);
  });
  panel.appendChild(grid);

  // Footer
  const footer = document.createElement('div');
  footer.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)';
  const cnt = document.createElement('span');
  cnt.id = 'cr-active-count';
  cnt.style.cssText = 'font-size:.75rem;color:var(--muted)';
  const footerBtns = document.createElement('div');
  footerBtns.style.display = 'flex';
  footerBtns.style.gap = '8px';
  const resetBtn = document.createElement('button');
  resetBtn.className = 'btn btn-secondary btn-sm';
  resetBtn.textContent = 'Reset to defaults';
  resetBtn.onclick = () => _applyPreset('full');
  const applyBtn = document.createElement('button');
  applyBtn.className = 'btn btn-primary btn-sm';
  applyBtn.textContent = 'Apply & Scan';
  applyBtn.onclick = () => { _toggleCriteriaPanel(); startScan(true); };
  footerBtns.appendChild(resetBtn);
  footerBtns.appendChild(applyBtn);
  footer.appendChild(cnt);
  footer.appendChild(footerBtns);
  panel.appendChild(footer);

  const meta = document.getElementById('scanner-meta');
  if (meta) meta.after(panel);

  _onCriteriaChange();
}

function _toggleCriteriaPanel() {
  let panel = document.getElementById('criteria-panel');
  if (!panel) { _renderCriteriaPanel(); panel = document.getElementById('criteria-panel'); }
  if (!panel) return;
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

async function loadScanner() {
  _loadScannerWatchlist();
  const [state] = await Promise.all([
    api('/api/scanner/status'),
    _loadNansenPanel(),
  ]);
  if (!state.ok) return;
  renderScannerPage(state.data);
  if (state.data.status === 'running') _startScanPoller();
  _renderCriteriaPanel();
}

// ── Nansen Smart Money Panel ──────────────────────────────────────────────────

async function _loadNansenPanel() {
  const panel = document.getElementById('nansen-panel');
  if (!panel) return;
  const res = await api('/api/nansen/movers');
  if (!res.ok || !res.data.configured) return;
  const d = res.data;
  if (!d.accumulators.length && !d.distributors.length) return;

  panel.style.display = 'block';

  const inner = document.createElement('div');
  inner.style.cssText = 'background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px';

  const hdr = document.createElement('div');
  hdr.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:10px';
  const title = document.createElement('span');
  title.style.cssText = 'font-weight:700;font-size:.88rem;color:var(--text)';
  title.textContent = '🐋 Nansen Smart Money';
  const sub = document.createElement('span');
  sub.style.cssText = 'font-size:.72rem;color:var(--muted)';
  sub.textContent = `${d.total_screened} tokens screened · ${d.eligible} with 5+ wallets · ${d.cached_at}`;
  hdr.appendChild(title);
  hdr.appendChild(sub);
  inner.appendChild(hdr);

  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:12px';

  // Accumulators
  const accCol = document.createElement('div');
  const accTitle = document.createElement('div');
  accTitle.style.cssText = 'font-size:.72rem;font-weight:700;color:var(--accent3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px';
  accTitle.textContent = '↑ Accumulating';
  accCol.appendChild(accTitle);
  d.accumulators.slice(0, 6).forEach(t => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:space-between;font-size:.77rem;padding:2px 0;border-bottom:1px solid var(--border)';
    const sym = document.createElement('span');
    sym.style.fontWeight = '600';
    sym.textContent = t.symbol + ' (' + t.chain.slice(0,3).toUpperCase() + ')';
    const nf = document.createElement('span');
    nf.style.color = 'var(--accent3)';
    nf.textContent = '$' + (t.netflow_usd / 1000).toFixed(0) + 'k · ' + t.nof_traders + ' wallets';
    row.appendChild(sym);
    row.appendChild(nf);
    accCol.appendChild(row);
  });
  grid.appendChild(accCol);

  // Distributors
  const distCol = document.createElement('div');
  const distTitle = document.createElement('div');
  distTitle.style.cssText = 'font-size:.72rem;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px';
  distTitle.textContent = '↓ Distributing';
  distCol.appendChild(distTitle);
  d.distributors.slice(0, 5).forEach(t => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:space-between;font-size:.77rem;padding:2px 0;border-bottom:1px solid var(--border)';
    const sym = document.createElement('span');
    sym.style.fontWeight = '600';
    sym.textContent = t.symbol + ' (' + t.chain.slice(0,3).toUpperCase() + ')';
    const nf = document.createElement('span');
    nf.style.color = 'var(--red)';
    nf.textContent = '$' + (Math.abs(t.netflow_usd) / 1000).toFixed(0) + 'k · ' + t.nof_traders + ' wallets';
    row.appendChild(sym);
    row.appendChild(nf);
    distCol.appendChild(row);
  });
  grid.appendChild(distCol);

  inner.appendChild(grid);
  panel.textContent = '';
  panel.appendChild(inner);
}

async function startScan(force) {
  const btn = document.getElementById('btn-scan');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Starting…'; }
  const minScore = parseInt(document.getElementById('scan-min-score')?.value || '6');
  const criteria = _readCriteriaFromCheckboxes();
  _saveCriteria(criteria);
  const res = await api('/api/scanner/run', 'POST', { force: !!force, min_score: minScore, criteria });
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
  }, 2000);
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

  let statusText = '';
  let statusColor = 'var(--muted)';
  if (running) {
    const stageLabel = state.stage_label || 'Scanning…';
    const detail     = state.stage_detail || '';
    statusText  = '⏳ ' + stageLabel + (detail ? ' — ' + detail : '');
    statusColor = 'var(--yellow)';
  } else if (done) {
    const ago  = state.completed_at ? Math.round((Date.now()/1000 - state.completed_at)/60) : 0;
    const dur  = state.duration_sec ? ` in ${state.duration_sec}s` : '';
    const n    = (state.setups||[]).length;
    const filt = state.after_filter > 0 ? ` · ${state.after_filter} to AI` : '';
    statusText  = `Last scan ${ago<1?'just now':ago+'m ago'}${dur} · ${state.scanned||0} symbols${filt} · ${n} setup${n!==1?'s':''} found`;
    statusColor = n ? 'var(--accent3)' : 'var(--muted)';
  } else if (state.status === 'error') {
    statusText  = 'Error: scan failed';
    statusColor = 'var(--red)';
  } else {
    statusText  = 'No scan run yet.';
  }

  // Rebuild DOM instead of innerHTML to avoid hook
  el.textContent = '';

  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:12px;flex-wrap:wrap';

  // Min score selector
  const scoreWrap = document.createElement('div');
  scoreWrap.style.cssText = 'display:flex;align-items:center;gap:6px';
  const scoreLbl = document.createElement('label');
  scoreLbl.style.cssText = 'font-size:.78rem;color:var(--muted);white-space:nowrap';
  scoreLbl.textContent = 'Min score';
  const scoreEl = document.createElement('select');
  scoreEl.id = 'scan-min-score';
  scoreEl.disabled = running;
  scoreEl.style.cssText = 'padding:5px 8px;font-size:.82rem;background:var(--bg2);border:1px solid var(--border);border-radius:6px;color:var(--text)';
  const activeMins = state.min_score ?? 6;
  [1,2,3,4,5,6,7,8,9].forEach(n => {
    const opt = document.createElement('option');
    opt.value = n; opt.textContent = n + '+';
    if (n === activeMins) opt.selected = true;
    scoreEl.appendChild(opt);
  });
  scoreWrap.appendChild(scoreLbl); scoreWrap.appendChild(scoreEl);
  row.appendChild(scoreWrap);

  // Scan button
  const scanBtn = document.createElement('button');
  scanBtn.className = 'btn btn-primary';
  scanBtn.id = 'btn-scan';
  scanBtn.disabled = running;
  scanBtn.textContent = running ? '⏳ Scanning…' : '🔍 Scan Now';
  scanBtn.onclick = () => startScan(false);
  row.appendChild(scanBtn);

  // Re-scan button
  if (done) {
    const rsBtn = document.createElement('button');
    rsBtn.className = 'btn btn-secondary btn-sm';
    rsBtn.textContent = '🔄 Re-scan';
    rsBtn.onclick = () => startScan(true);
    row.appendChild(rsBtn);
  }

  // Criteria button with active count badge
  const saved = _loadCriteria() || {};
  const activeCount = SCAN_CRITERIA.filter(c => saved[c.key] !== false).length;
  const crBtn = document.createElement('button');
  crBtn.className = 'btn btn-secondary btn-sm';
  crBtn.id = 'btn-criteria';
  crBtn.onclick = _toggleCriteriaPanel;
  const crBtnTxt = document.createElement('span');
  crBtnTxt.textContent = '⚙ Criteria';
  crBtn.appendChild(crBtnTxt);
  if (activeCount < SCAN_CRITERIA.length) {
    const badge = document.createElement('span');
    badge.style.cssText = 'font-size:.7rem;background:rgba(255,179,0,.15);color:var(--yellow);padding:2px 7px;border-radius:10px;margin-left:5px';
    badge.textContent = activeCount + '/' + SCAN_CRITERIA.length;
    crBtn.appendChild(badge);
  }
  row.appendChild(crBtn);

  // Status text
  const status = document.createElement('span');
  status.style.cssText = 'font-size:.82rem;color:' + statusColor;
  status.textContent = statusText;
  row.appendChild(status);

  el.appendChild(row);

  const sub = document.createElement('div');
  sub.style.cssText = 'font-size:.78rem;color:var(--muted);margin-top:6px';
  sub.textContent = (window._scannerWatchlistCount||100) + ' symbols · scores ' + activeMins + '–10 · results cached 30 min · click a row for details';
  el.appendChild(sub);
}

function _buildProgressBlock(state) {
  const stage = state.stage || 0;
  const pct   = Math.min(100, state.stage_progress || 0);
  const detail = state.stage_detail || '';

  const stages = [
    { n:1, label:'Confluence filter',   sub:'Parallel OHLCV + TA for all symbols' },
    { n:2, label:'Quality gate',        sub:'RSI · ADX · S/R · signal count' },
    { n:3, label:'AI scoring',          sub:'Haiku pre-filter → Sonnet batch' },
  ];

  const wrap = document.createElement('div');
  wrap.className = 'prog-container';

  const row = document.createElement('div');
  row.className = 'prog-stages';

  stages.forEach((s, idx) => {
    if (idx > 0) {
      const arr = document.createElement('div');
      arr.className = 'prog-arrow';
      arr.textContent = '→';
      row.appendChild(arr);
    }
    const done   = stage > s.n;
    const active = stage === s.n;
    const div = document.createElement('div');
    div.className = 'prog-stage' + (done ? ' done' : active ? ' active' : '');
    const icon = document.createElement('div');
    icon.className = 'prog-stage-icon';
    icon.textContent = done ? '✓' : active ? '⏳' : String(s.n);
    const lbl = document.createElement('div');
    lbl.className = 'prog-stage-label';
    lbl.textContent = s.label;
    const sub = document.createElement('div');
    sub.className = 'prog-stage-sub';
    sub.textContent = active && detail ? detail : s.sub;
    div.appendChild(icon);
    div.appendChild(lbl);
    div.appendChild(sub);
    row.appendChild(div);
  });
  wrap.appendChild(row);

  // Progress bar for stages with pct data
  if ((stage === 1 || stage === 3) && pct > 0) {
    const barWrap = document.createElement('div');
    barWrap.className = 'prog-bar-wrap';
    const fill = document.createElement('div');
    fill.className = 'prog-bar-fill';
    fill.style.width = pct + '%';
    barWrap.appendChild(fill);
    wrap.appendChild(barWrap);
  }

  // Detail line for stages 2+
  if (stage >= 2 && detail) {
    const det = document.createElement('div');
    det.className = 'prog-detail';
    det.textContent = detail;
    wrap.appendChild(det);
  }

  return wrap;
}

function renderScannerResults(state) {
  const el = document.getElementById('scanner-results');
  if (!el) return;

  if (state.status === 'idle') {
    const wrap = document.createElement('div');
    wrap.className = 'no-positions';
    wrap.innerHTML = '<div class="icon">🔍</div>';
    const t = document.createElement('div');
    t.style.cssText = 'font-weight:600;margin-bottom:6px';
    t.textContent = 'Ready to scan';
    const d = document.createElement('div');
    d.style.cssText = 'color:var(--muted);font-size:.85rem';
    d.textContent = 'Scans ' + (window._scannerWatchlistCount||100) + ' symbols. Use ⚙ Criteria to customise which checks apply. Click a result row for entry / SL / TP details.';
    wrap.appendChild(t); wrap.appendChild(d);
    el.textContent = '';
    el.appendChild(wrap);
    return;
  }
  if (state.status === 'running') {
    const wrap = document.createElement('div');
    wrap.className = 'no-positions';
    wrap.style.padding = '28px 20px';
    wrap.appendChild(_buildProgressBlock(state));
    el.textContent = '';
    el.appendChild(wrap);
    return;
  }
  if (state.status === 'error') {
    el.textContent = 'Scan error — check server logs';
    el.style.cssText = 'color:var(--red);padding:24px';
    return;
  }

  _scanSetups = state.setups || [];
  _scanExpandedIdx = null;

  if (!_scanSetups.length) {
    el.innerHTML = `<div class="no-positions">
      <div class="icon">😴</div>
      <div style="font-weight:600;margin-bottom:6px">No setups found at selected threshold</div>
      <div style="color:var(--muted);font-size:.85rem">
        No symbols passed all three stages right now.<br>
        Market may be choppy or overextended — try again after a key level test.
      </div></div>`;
    return;
  }

  el.innerHTML = `
    <div style="font-size:.8rem;color:var(--muted);margin-bottom:12px">
      ${_scanSetups.length} setup${_scanSetups.length!==1?'s':''} · min score ${_scanLastState?.min_score??6}/10 · sorted by score · click a row for details
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

  const quickOnly = s.quick_score_only;
  const row = `<tr class="scanner-row${quickOnly?' scanner-row-quick':''}" onclick="toggleScanDetail(${i})" data-idx="${i}"
    title="${quickOnly ? 'Quick score only (Haiku) — no full breakdown available' : ''}">
    <td><span style="font-size:1rem;font-weight:800;color:${scoreCol}">${score}</span>
        <span style="font-size:.7rem;color:var(--muted)">/10</span>
        ${quickOnly ? '<span style="font-size:.6rem;color:var(--muted);margin-left:3px">⚡</span>' : ''}</td>
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

  const scoreColor = s.setup_score>=9?'var(--accent3)':s.setup_score>=7?'var(--yellow)':'var(--muted)';
  const setupData  = JSON.stringify(s);

  return `<div class="scan-detail-panel">
    <div class="scan-dp-header">
      <span style="font-weight:700;font-size:.95rem">${sym.replace('USDT','')}USDT
        <span style="color:${isLong?'var(--accent3)':'var(--red)'}">${s.direction}</span> —
        <span style="color:${scoreColor}">${s.setup_score}/10 ${scoreLabel}</span>
      </span>
      ${s.quick_score_only ? `<span style="font-size:.7rem;color:var(--muted);margin-left:8px">⚡ Quick score (Haiku) — no full breakdown</span>` : ''}
    </div>

    ${s.why_this_score ? `
      <div class="scan-dp-score-reason">
        <span style="font-size:.7rem;font-weight:700;color:${scoreColor};text-transform:uppercase;letter-spacing:.04em">Why ${s.setup_score}/10</span>
        <div style="margin-top:4px;font-size:.8rem;color:var(--text);line-height:1.55">${s.why_this_score}</div>
      </div>` : ''}

    ${s.confluence_summary ? `<div class="scan-dp-confluence">${s.confluence_summary}</div>` : ''}
    ${s.nansen ? (() => {
      const acc = s.nansen.direction === 'accumulating';
      const cls = acc ? 'nansen-acc' : 'nansen-dist';
      const nf  = (Math.abs(s.nansen.netflow_usd) / 1000).toFixed(0);
      return `<div class="nansen-badge ${cls}">🐋 Smart money <strong>${s.nansen.direction}</strong> — netflow $${nf}k · ${s.nansen.nof_traders} wallets (${s.nansen.strength})</div>`;
    })() : ''}

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

    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center">
      <button class="btn btn-primary btn-sm"
        onclick="openScannerChart('${sym}',${setupData});event.stopPropagation()">📊 Chart with Levels</button>
      <button class="btn btn-secondary btn-sm"
        onclick="_sendToCallAnalyzer(${JSON.stringify(prefill)});event.stopPropagation()">📋 Analyze</button>
      <span style="font-size:.68rem;color:var(--border)">
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

// ── Chart with entry/SL/TP levels ────────────────────────────────────────────

function openScannerChart(sym, setup, evt) {
  if (evt) evt.stopPropagation();
  const ent = setup.entry_zone || {};
  const entLow  = parseFloat(ent.low  || 0);
  const entHigh = parseFloat(ent.high || 0);
  // Use midpoint of entry zone as the "entry" price line
  const entryPrice = (entLow && entHigh) ? (entLow + entHigh) / 2
                   : (entLow || entHigh);

  const trades = [];
  if (entryPrice) {
    trades.push({
      dir:   setup.direction || 'Long',
      entry: entryPrice,
      sl:    setup.sl_price  ? parseFloat(setup.sl_price)  : null,
      tp1:   setup.tp1_price ? parseFloat(setup.tp1_price) : null,
      tp2:   setup.tp2_price ? parseFloat(setup.tp2_price) : null,
    });
  }

  let url = `/chart?symbol=${encodeURIComponent(sym)}&timeframe=${setup.timeframe||'4H'}`;
  if (trades.length) url += '&trades=' + encodeURIComponent(JSON.stringify(trades));
  window.open(url, 'chart_' + sym,
    'width=1060,height=680,resizable=yes,scrollbars=no,toolbar=no,menubar=no,location=no');
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
