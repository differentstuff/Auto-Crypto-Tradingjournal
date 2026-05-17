// ══════════════════════════════════════════════════════════════════════════════
// SETUP SCANNER — rated setups 6-10/10, live progress, custom criteria
// ══════════════════════════════════════════════════════════════════════════════

let _scanPollInterval  = null;
let _scanLastState     = null;
let _scanExpandedIdx   = null;
let _scanSetups        = [];
let _pendingSingleScan = null;   // string (symbol) when a single-coin scan is queued

// ── Criteria definition ───────────────────────────────────────────────────────
// group: 'trend' | 'momentum' | 'flow' | 'risk' | 'context'

const SCAN_CRITERIA = [
  { key:'ema_stack',  group:'trend',    icon:'📈', label:'EMA Stack',
    desc:'20/50/200 EMA alignment with price. Confirms trend phase and direction. Primary filter for continuation setups — disable when trading against the trend (reversals).' },
  { key:'adx',        group:'trend',    icon:'💪', label:'ADX Strength',
    desc:'Rejects flat/choppy markets (ADX < 18). Ensures a real trend exists before scoring. Critical for continuations. Disable for reversals — low ADX is favourable there.' },
  { key:'sr_anchor',  group:'trend',    icon:'🏗', label:'S/R Anchor',
    desc:'Entry must be within 4xATR of a named support/resistance level. Structural discipline. Disable for news-driven or low-cap momentum moves.' },
  { key:'rsi',        group:'momentum', icon:'⚡', label:'RSI Zone',
    desc:'Penalises overextended RSI (above 78 long / below 22 short) for continuations. For reversals RSI extremes are conviction, not a penalty.' },
  { key:'macd',       group:'momentum', icon:'📊', label:'MACD Signal',
    desc:'Crossover direction and histogram trend as a 4H momentum signal. Strong weighting for breakouts (growing histogram = expanding momentum) and continuations.' },
  { key:'wavetrend',  group:'momentum', icon:'🌊', label:'WaveTrend / VMC',
    desc:'VMC Cipher A/B oscillator crossover. Primary trigger for reversals — gold buy = highest conviction. Less relevant for breakouts where price leads.' },
  { key:'volume',     group:'flow',     icon:'📦', label:'Volume Confirm',
    desc:'High volume (above 1.5x) amplifies score +0.5. Low volume (below 0.7x) dampens -0.25. Non-negotiable for real breakouts — no volume means fakeout risk.' },
  { key:'atr_sl',     group:'risk',     icon:'🛡', label:'ATR SL Floor',
    desc:'Caps score at 6 when SL is tighter than 1xATR from entry. Stops inside the noise floor get hunted before the move begins. Almost always leave on.' },
  { key:'rr_minimum', group:'risk',     icon:'⚖', label:'R:R Minimum 2:1',
    desc:'Caps score at 6 for R:R below 2:1. Requires 2.5:1 for score 7+. The most important quality gate — poor R:R kills edge even when direction is right.' },
  { key:'funding',    group:'risk',     icon:'💸', label:'Funding Penalty',
    desc:'Penalises score -1/-2 for crowded funding (above 0.05% / 0.1%). High funding = late entry, crowded trade. Always enable for perpetual futures.' },
  { key:'fear_greed', group:'context',  icon:'🌍', label:'Fear & Greed',
    desc:'Score adjustment for extreme sentiment (below 20 or above 80). Useful when macro strongly biases one direction. Lower impact than structural signals.' },
];

const SCAN_GROUPS = [
  { key:'trend',    label:'Trend & Structure',  color:'#4fc3f7', hint:'Does a trend exist at a meaningful level?' },
  { key:'momentum', label:'Momentum Signals',   color:'#6c63ff', hint:'What are the oscillators saying?' },
  { key:'flow',     label:'Flow & Volume',      color:'#4a90d9', hint:'Is there real participation behind the move?' },
  { key:'risk',     label:'Risk Quality Gates', color:'#26d96b', hint:'Hard limits — SL structure and R:R' },
  { key:'context',  label:'Market Context',     color:'#ffb300', hint:'External sentiment overlay' },
];

const SCAN_PRESETS = [
  { key:'all',
    label:'All Checks', icon:'✨',
    hint:'Strictest filter — every signal active',
    keys: null },
  { key:'continuation',
    label:'Continuation', icon:'📈',
    hint:'EMA stack + ADX required · RSI sweet spot · no WaveTrend needed',
    keys:{ema_stack:true, adx:true, rsi:true, macd:true, volume:true, sr_anchor:true, atr_sl:true, rr_minimum:true, wavetrend:false, funding:false, fear_greed:false} },
  { key:'reversal',
    label:'Reversal', icon:'🔄',
    hint:'WaveTrend + RSI extremes primary · EMA/ADX not required',
    keys:{wavetrend:true, rsi:true, sr_anchor:true, atr_sl:true, rr_minimum:true, macd:true, volume:true, funding:true, fear_greed:true, ema_stack:false, adx:false} },
  { key:'breakout',
    label:'Breakout', icon:'🚀',
    hint:'Volume non-negotiable · MACD momentum · S/R break required',
    keys:{volume:true, macd:true, rsi:true, sr_anchor:true, adx:true, atr_sl:true, rr_minimum:true, wavetrend:false, ema_stack:false, funding:false, fear_greed:false} },
  { key:'scalp',
    label:'Scalp / News', icon:'⚡',
    hint:'Fast moves · minimal structure · volume + RSI only',
    keys:{rsi:true, volume:true, atr_sl:true, rr_minimum:true, ema_stack:false, adx:false, sr_anchor:false, macd:false, wavetrend:false, funding:false, fear_greed:false} },
];

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
    cr[c.key] = el ? el.getAttribute('data-active') !== '0' : true;
  });
  return cr;
}

function _pillSetActive(el, active) {
  if (!el) return;
  el.setAttribute('data-active', active ? '1' : '0');
  const color = el.getAttribute('data-color') || '#6c63ff';
  if (active) {
    el.style.background  = _colorAlpha(color, 0.12);
    el.style.borderColor = color;
    el.style.color       = color;
    el.style.opacity     = '1';
  } else {
    el.style.background  = 'var(--bg3)';
    el.style.borderColor = 'var(--border)';
    el.style.color       = 'var(--muted)';
    el.style.opacity     = '0.55';
  }
}

function _colorAlpha(hex, a) {
  const h = hex.replace('#', '');
  if (h.length !== 6) return 'rgba(108,99,255,' + a + ')';
  const r = parseInt(h.slice(0,2), 16);
  const g = parseInt(h.slice(2,4), 16);
  const b = parseInt(h.slice(4,6), 16);
  return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
}

function _applyPreset(presetKey) {
  const p = SCAN_PRESETS.find(x => x.key === presetKey);
  if (!p) return;
  SCAN_CRITERIA.forEach(c => {
    const active = p.keys === null ? true : (p.keys[c.key] !== false);
    _pillSetActive(document.getElementById('cr-' + c.key), active);
  });
  _onCriteriaChange();
  document.querySelectorAll('.cr-preset-tab').forEach(btn => {
    btn.classList.toggle('cr-preset-active', btn.getAttribute('data-pkey') === presetKey);
  });
}

function _onCriteriaChange() {
  const cr = _readCriteriaFromCheckboxes();
  _saveCriteria(cr);
  let matched = null;
  for (const p of SCAN_PRESETS) {
    if (p.keys === null) {
      if (SCAN_CRITERIA.every(c => cr[c.key] !== false)) { matched = p.key; break; }
    } else {
      if (SCAN_CRITERIA.every(c => (cr[c.key] !== false) === (p.keys[c.key] !== false))) { matched = p.key; break; }
    }
  }
  document.querySelectorAll('.cr-preset-tab').forEach(btn => {
    btn.classList.toggle('cr-preset-active', btn.getAttribute('data-pkey') === (matched || ''));
  });
  const cnt = document.getElementById('cr-active-count');
  if (cnt) {
    const n = SCAN_CRITERIA.filter(c => cr[c.key] !== false).length;
    cnt.textContent = n + ' / ' + SCAN_CRITERIA.length + ' active';
    cnt.style.color = n === SCAN_CRITERIA.length ? 'var(--muted)' : 'var(--yellow)';
  }
}

// ── Criteria panel builder ────────────────────────────────────────────────────

function _injectCriteriaCSS() {
  if (document.getElementById('cr-style')) return;
  const s = document.createElement('style');
  s.id = 'cr-style';
  s.textContent = `
    #criteria-panel{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin-bottom:16px;box-shadow:0 4px 20px rgba(0,0,0,.35)}
    .cr-panel-hdr{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}
    .cr-panel-title{font-size:.95rem;font-weight:700;color:var(--text)}
    .cr-panel-sub{font-size:.74rem;color:var(--muted);margin-top:3px;max-width:520px;line-height:1.5}
    .cr-close-btn{background:none;border:none;color:var(--muted);font-size:1rem;cursor:pointer;padding:2px 6px;border-radius:4px;line-height:1}
    .cr-close-btn:hover{color:var(--text);background:var(--bg3)}
    .cr-preset-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;padding:12px 14px;background:var(--bg3);border-radius:8px;border:1px solid var(--border)}
    .cr-preset-tab{display:flex;flex-direction:column;align-items:flex-start;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:7px 12px;cursor:pointer;transition:.15s;min-width:100px;flex:1}
    .cr-preset-tab:hover{border-color:var(--accent);background:var(--bg)}
    .cr-preset-active{border-color:var(--accent)!important;background:rgba(108,99,255,.1)!important}
    .cr-preset-name{font-size:.8rem;font-weight:700;color:var(--text)}
    .cr-preset-hint{font-size:.68rem;color:var(--muted);margin-top:3px;line-height:1.35}
    .cr-group{margin-bottom:14px}
    .cr-group-hdr{display:flex;align-items:center;gap:8px;margin-bottom:8px}
    .cr-group-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
    .cr-group-label{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
    .cr-group-hint{font-size:.68rem;color:var(--muted);margin-left:auto}
    .cr-pills{display:flex;flex-wrap:wrap;gap:7px}
    .cr-pill{display:flex;flex-direction:column;border:1px solid var(--border);border-radius:8px;padding:8px 12px;cursor:pointer;transition:.15s;min-width:120px;flex:1;max-width:210px;background:var(--bg3);color:var(--muted);opacity:.55}
    .cr-pill:hover{opacity:1!important}
    .cr-pill-top{display:flex;align-items:center;gap:6px;margin-bottom:4px}
    .cr-pill-icon{font-size:.95rem;line-height:1}
    .cr-pill-name{font-size:.8rem;font-weight:700;line-height:1.2}
    .cr-pill-desc{font-size:.68rem;line-height:1.4;color:inherit;opacity:.85}
    .cr-footer{display:flex;align-items:center;justify-content:space-between;margin-top:14px;padding-top:12px;border-top:1px solid var(--border)}
  `;
  document.head.appendChild(s);
}

function _renderCriteriaPanel() {
  const old = document.getElementById('criteria-panel');
  if (old) old.remove();
  _injectCriteriaCSS();

  const saved = _loadCriteria() || {};
  const panel = document.createElement('div');
  panel.id = 'criteria-panel';
  panel.style.display = 'none';

  // Header
  const hdr = document.createElement('div'); hdr.className = 'cr-panel-hdr';
  const hdrL = document.createElement('div');
  const title = document.createElement('div'); title.className = 'cr-panel-title';
  title.textContent = '🎯 Scoring Criteria';
  const sub = document.createElement('div'); sub.className = 'cr-panel-sub';
  sub.textContent = 'Select which signals Claude evaluates when scoring setups. Disabled criteria are skipped in the quality gate and prompt. Hover any pill for details.';
  hdrL.appendChild(title); hdrL.appendChild(sub);
  const closeBtn = document.createElement('button'); closeBtn.className = 'cr-close-btn';
  closeBtn.textContent = '✕'; closeBtn.onclick = _toggleCriteriaPanel;
  hdr.appendChild(hdrL); hdr.appendChild(closeBtn);
  panel.appendChild(hdr);

  // Strategy presets
  const presetRow = document.createElement('div'); presetRow.className = 'cr-preset-row';
  SCAN_PRESETS.forEach(p => {
    const tab = document.createElement('button'); tab.className = 'cr-preset-tab';
    tab.setAttribute('data-pkey', p.key);
    const name = document.createElement('div'); name.className = 'cr-preset-name';
    name.textContent = p.icon + '  ' + p.label;
    const hint = document.createElement('div'); hint.className = 'cr-preset-hint';
    hint.textContent = p.hint;
    tab.appendChild(name); tab.appendChild(hint);
    tab.onclick = () => _applyPreset(p.key);
    presetRow.appendChild(tab);
  });
  panel.appendChild(presetRow);

  // Grouped criteria pills
  SCAN_GROUPS.forEach(g => {
    const items = SCAN_CRITERIA.filter(c => c.group === g.key);
    if (!items.length) return;
    const section = document.createElement('div'); section.className = 'cr-group';
    const ghdr = document.createElement('div'); ghdr.className = 'cr-group-hdr';
    const dot = document.createElement('div'); dot.className = 'cr-group-dot';
    dot.style.background = g.color;
    const glabel = document.createElement('div'); glabel.className = 'cr-group-label';
    glabel.style.color = g.color; glabel.textContent = g.label;
    const ghint = document.createElement('div'); ghint.className = 'cr-group-hint';
    ghint.textContent = g.hint;
    ghdr.appendChild(dot); ghdr.appendChild(glabel); ghdr.appendChild(ghint);
    section.appendChild(ghdr);
    const pills = document.createElement('div'); pills.className = 'cr-pills';
    items.forEach(c => {
      const active = saved[c.key] !== false;
      const pill = document.createElement('div'); pill.className = 'cr-pill';
      pill.id = 'cr-' + c.key;
      pill.setAttribute('data-active', active ? '1' : '0');
      pill.setAttribute('data-color', g.color);
      pill.title = c.desc;
      const top = document.createElement('div'); top.className = 'cr-pill-top';
      const icon = document.createElement('span'); icon.className = 'cr-pill-icon';
      icon.textContent = c.icon;
      const nm = document.createElement('span'); nm.className = 'cr-pill-name';
      nm.textContent = c.label;
      top.appendChild(icon); top.appendChild(nm);
      const desc = document.createElement('div'); desc.className = 'cr-pill-desc';
      desc.textContent = c.desc.split('.')[0] + '.';
      pill.appendChild(top); pill.appendChild(desc);
      pill.onclick = () => {
        const now = pill.getAttribute('data-active') !== '0';
        _pillSetActive(pill, !now);
        _onCriteriaChange();
      };
      _pillSetActive(pill, active);
      pills.appendChild(pill);
    });
    section.appendChild(pills); panel.appendChild(section);
  });

  // Footer
  const footer = document.createElement('div'); footer.className = 'cr-footer';
  const cnt = document.createElement('span'); cnt.id = 'cr-active-count';
  cnt.style.cssText = 'font-size:.76rem;color:var(--muted)';
  const btns = document.createElement('div'); btns.style.cssText = 'display:flex;gap:8px';
  const resetBtn = document.createElement('button'); resetBtn.className = 'btn btn-secondary btn-sm';
  resetBtn.textContent = 'Reset'; resetBtn.onclick = () => _applyPreset('all');
  const applyBtn = document.createElement('button'); applyBtn.className = 'btn btn-primary btn-sm';
  applyBtn.textContent = 'Apply & Scan';
  applyBtn.onclick = () => { _toggleCriteriaPanel(); startScan(true); };
  btns.appendChild(resetBtn); btns.appendChild(applyBtn);
  footer.appendChild(cnt); footer.appendChild(btns);
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
  loadScannerFeedback();
}

async function loadScannerFeedback() {
    const el = document.getElementById('scanner-feedback');
    if (!el) return;
    try {
        const r = await fetch('/api/scanner/feedback');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = '';
            return;
        }
        const fb = d.data;
        const msgMap = {
            raise_threshold: 'Warning: High FP rate — consider raising scanner min score',
            lower_threshold: 'Strong accuracy — you can lower min score for more setups',
            ok: 'Signal accuracy within normal range',
        };
        // All msg values are hardcoded strings — no user-controlled content
        el.textContent = (msgMap[fb.recommendation] || '') + ' (' + fb.sample_size + ' trades analyzed)';
        el.style.color = fb.recommendation === 'raise_threshold' ? '#f0a030' : '#4caf50';
    } catch(e) {
        // non-fatal
    }
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
    if (res.data.status !== 'running') {
      _stopScanPoller();
      _firePendingSingleScan();
    }
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

  // Cancel button — only visible while a scan is running
  if (running) {
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-sm';
    cancelBtn.style.cssText = 'background:rgba(239,83,80,.15);color:var(--red);border:1px solid rgba(239,83,80,.35)';
    cancelBtn.textContent = '✕ Cancel';
    cancelBtn.onclick = async () => {
      cancelBtn.disabled = true;
      cancelBtn.textContent = 'Cancelling…';
      await api('/api/scanner/cancel', 'POST');
    };
    row.appendChild(cancelBtn);
  }

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

  // ── Macro context row ─────────────────────────────────────────────────────
  const mc = state.macro_ctx;
  if (mc && (mc.vix != null || mc.fear_greed != null || mc.cap_applied != null)) {
    const macroRow = document.createElement('div');
    macroRow.style.cssText = 'font-size:.76rem;color:var(--text-muted,var(--muted));margin-top:5px;display:flex;align-items:center;gap:6px;flex-wrap:wrap';

    const vixPart  = mc.vix        != null ? `VIX ${mc.vix.toFixed != null ? mc.vix.toFixed(1) : mc.vix}` : null;
    const fgPart   = mc.fear_greed != null ? `F&G ${mc.fear_greed}` : (mc.fg != null ? `F&G ${mc.fg}` : null);
    const capVal   = mc.cap_applied != null ? parseFloat(mc.cap_applied) : null;
    const capPart  = capVal != null ? (capVal >= 10 ? 'No cap' : `Cap ${capVal.toFixed(1)}`) : null;

    const parts = [vixPart, fgPart, capPart].filter(Boolean);
    if (parts.length) {
      const macroLabel = document.createElement('span');
      macroLabel.style.color = 'var(--muted)';
      macroLabel.textContent = 'Macro:';
      macroRow.appendChild(macroLabel);
      const macroInfo = document.createElement('span');
      macroInfo.textContent = parts.join(' · ');
      macroRow.appendChild(macroInfo);
    }

    const warnings = state.macro_warnings || [];
    if (warnings.length) {
      warnings.forEach(w => {
        const badge = document.createElement('span');
        badge.style.cssText = 'color:var(--yellow);font-size:.73rem;padding:1px 6px;background:rgba(255,179,0,.1);border-radius:4px';
        badge.textContent = '⚠ ' + w;
        macroRow.appendChild(badge);
      });
    }

    if (macroRow.childNodes.length) el.appendChild(macroRow);
  }

  // ── Single-coin scan row ──────────────────────────────────────────────────
  const singleRow = document.createElement('div');
  singleRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:10px';

  const symInput = document.createElement('input');
  symInput.type = 'text';
  symInput.id = 'scan-single-symbol';
  symInput.placeholder = 'Symbol e.g. BTC';
  symInput.style.cssText = [
    'padding:5px 10px', 'font-size:.82rem', 'background:var(--bg2)',
    'border:1px solid var(--border)', 'border-radius:6px', 'color:var(--text)',
    'width:130px', 'text-transform:uppercase',
  ].join(';');
  symInput.addEventListener('input', () => {
    symInput.value = symInput.value.toUpperCase();
    const b = document.getElementById('btn-scan-single');
    if (b) b.disabled = !symInput.value.trim() || !!_pendingSingleScan;
  });
  symInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('btn-scan-single')?.click();
  });

  const singleBtn = document.createElement('button');
  singleBtn.className = 'btn btn-secondary btn-sm';
  singleBtn.id = 'btn-scan-single';
  singleBtn.disabled = true;
  singleBtn.textContent = _pendingSingleScan ? '⏳ Queued…' : '🔍 Scan Symbol';
  singleBtn.onclick = _startSingleScan;

  singleRow.appendChild(symInput);
  singleRow.appendChild(singleBtn);
  el.appendChild(singleRow);

  // Attach symbol autocomplete after the input is in the DOM
  if (typeof _attachSymbolPicker === 'function') _attachSymbolPicker('scan-single-symbol');

  // Queue badge — shown when a single-coin scan is waiting
  if (_pendingSingleScan) {
    const badge = document.createElement('div');
    badge.id = 'scan-queue-badge';
    badge.style.cssText = [
      'display:flex', 'align-items:center', 'gap:8px',
      'padding:6px 12px', 'margin-top:6px',
      'background:rgba(255,179,0,.1)', 'border:1px solid rgba(255,179,0,.3)',
      'border-radius:8px', 'font-size:.8rem', 'color:var(--yellow)',
    ].join(';');
    const msg = document.createElement('span');
    msg.textContent = '⏳ ' + _pendingSingleScan + ' queued — waiting for current scan to finish';
    const xBtn = document.createElement('button');
    xBtn.textContent = '✕';
    xBtn.style.cssText = 'background:none;border:none;color:var(--yellow);cursor:pointer;font-size:.9rem;padding:0 0 0 4px;line-height:1';
    xBtn.onclick = _clearPendingSingleScan;
    badge.appendChild(msg);
    badge.appendChild(xBtn);
    el.appendChild(badge);
  }
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

  // ETA estimation (shown only between 5% and 95% overall progress)
  if (state.started_at && state.status === 'running') {
    const elapsed    = Date.now() / 1000 - state.started_at;
    const overallPct = stage === 1 ? pct * 0.40
                     : stage === 2 ? 40 + pct * 0.20
                     : stage === 3 ? 60 + pct * 0.40
                     : 0;
    const frac = overallPct / 100;
    if (frac > 0.05 && frac < 0.95 && elapsed > 5) {
      const totalEst  = elapsed / frac;
      const remaining = Math.max(0, Math.round(totalEst - elapsed));
      const etaText   = remaining < 60
        ? '~' + remaining + 's remaining'
        : '~' + Math.ceil(remaining / 60) + 'm remaining';
      const eta = document.createElement('div');
      eta.style.cssText = 'font-size:.75rem;color:var(--muted);margin-top:4px';
      eta.textContent = etaText;
      wrap.appendChild(eta);
    }
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

  renderScannerCards(_scanSetups);
}

// Mobile: compact cards for narrow viewports (DOM-only, no innerHTML)
function renderScannerCards(setups) {
    const container = document.getElementById('scanner-cards');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!setups || !setups.length) {
        const msg = document.createElement('p');
        msg.className = 'muted';
        msg.style.padding = '16px';
        msg.textContent = 'No setups found.';
        container.appendChild(msg);
        return;
    }

    setups.forEach(s => {
        const score = s.setup_score || 0;
        const ez    = s.entry_zone || {};
        const card  = document.createElement('div');
        card.className = 'scan-card';

        const hdr = document.createElement('div');
        hdr.className = 'scan-card-header';

        const symEl = document.createElement('span');
        symEl.className = 'scan-card-symbol';
        symEl.textContent = s._symbol || s.symbol || '?';

        const badge = document.createElement('span');
        const badgeCls = score >= 8 ? 'hi' : score >= 6 ? 'mid' : 'low';
        badge.className = 'scan-score-badge ' + badgeCls;
        badge.textContent = score + '/10';

        hdr.appendChild(symEl);
        hdr.appendChild(badge);

        const rows = [
            ['Direction', s.direction || '—'],
            ['Entry', ez.low && ez.high
                ? parseFloat(ez.low).toFixed(4) + '–' + parseFloat(ez.high).toFixed(4)
                : '—'],
            ['SL',  s.sl_price  ? parseFloat(s.sl_price).toFixed(4)  : '—'],
            ['TP1', s.tp1_price ? parseFloat(s.tp1_price).toFixed(4) : '—'],
            ['R:R', s.rr_ratio  || '—'],
        ];

        const rowsEl = document.createElement('div');
        rows.forEach(([label, value]) => {
            const row = document.createElement('div');
            row.className = 'scan-card-row';
            const lbl = document.createElement('span');
            lbl.className = 'lbl';
            lbl.textContent = label;
            const val = document.createElement('span');
            val.textContent = value;
            row.appendChild(lbl);
            row.appendChild(val);
            rowsEl.appendChild(row);
        });

        card.appendChild(hdr);
        card.appendChild(rowsEl);

        if (s.summary) {
            const sumEl = document.createElement('div');
            sumEl.style.cssText = 'font-size:12px;color:var(--text-muted,#888);margin-top:8px;line-height:1.4';
            sumEl.textContent = (s.summary || '').substring(0, 120);
            card.appendChild(sumEl);
        }

        container.appendChild(card);
    });
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

  const rr = parseFloat(s.rr_ratio) || 0;
  const rrColor = rr >= 3.0 ? 'var(--accent3)' : rr >= 2.0 ? 'var(--muted)' : 'var(--red)';

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
    <td style="font-size:.82rem;font-weight:600"><span style="color:${rrColor}">${rr ? rr.toFixed(1) + 'R' : '—'}</span></td>
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
      <div class="scan-dp-rat">${mdToHtml(rationale||'')}</div>
    </div>`;
  }

  const entryColor = 'var(--accent2)';
  const entText = (ent.low && ent.high && ent.low !== ent.high)
    ? `${fmtSP(ent.low)} – ${fmtSP(ent.high)}` : fmtSP(ent.low || ent.high);

  const conditions = (s.key_conditions||[]).map(c => `<span class="scan-dp-cond">✓ ${_esc(c)}</span>`).join('');
  const risks      = (s.risks||[]).map(r => `<span class="scan-dp-risk">⚠ ${_esc(r)}</span>`).join('');

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
  // setupData intentionally not used — buttons reference _scanSetups[i] to avoid
  // JSON double-quote injection breaking the onclick HTML attribute.

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
        <div style="margin-top:4px;font-size:.8rem;color:var(--text);line-height:1.55">${mdToHtml(s.why_this_score)}</div>
      </div>` : ''}

    ${s.confluence_summary ? `<div class="scan-dp-confluence">${mdToHtml(s.confluence_summary)}</div>` : ''}
    ${s.nansen ? (() => {
      const acc = s.nansen.direction === 'accumulating';
      const cls = acc ? 'nansen-acc' : 'nansen-dist';
      const nf  = (Math.abs(s.nansen.netflow_usd) / 1000).toFixed(0);
      return `<div class="nansen-badge ${cls}">🐋 Smart money <strong>${s.nansen.direction}</strong> — netflow $${nf}k · ${s.nansen.nof_traders} wallets (${s.nansen.strength})</div>`;
    })() : ''}

    ${s.summary ? `<div class="scan-dp-summary" style="line-height:1.6">${mdToHtml(s.summary)}</div>` : ''}

    <div class="scan-dp-levels">
      <div class="scan-dp-level">
        <div class="scan-dp-lbl" style="color:${entryColor}">ENTRY ZONE</div>
        <div class="scan-dp-price" style="color:${entryColor}">${entText}</div>
        <div class="scan-dp-rat">${mdToHtml(ent.rationale||'')}</div>
      </div>
      ${lvl('STOP LOSS', s.sl_price, s.sl_rationale, 'var(--red)')}
      ${lvl('TAKE PROFIT 1', s.tp1_price, s.tp1_rationale, 'var(--accent3)')}
      ${lvl('TAKE PROFIT 2', s.tp2_price, s.tp2_rationale, 'var(--accent3)')}
    </div>

    ${conditions ? `<div class="scan-dp-conds">${conditions}</div>` : ''}
    ${risks      ? `<div class="scan-dp-risks">${risks}</div>`      : ''}

    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center">
      <button class="btn btn-primary btn-sm"
        onclick="openScannerChart('${sym}',_scanSetups[${i}]);event.stopPropagation()">📊 Chart with Levels</button>
      <button class="btn btn-secondary btn-sm"
        onclick="_sendToCallAnalyzer(${i});event.stopPropagation()">📋 Analyze</button>
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
  const w = window.open(url, 'chart_' + sym,
    'width=1060,height=680,resizable=yes,scrollbars=no,toolbar=no,menubar=no,location=no');
  if (!w) notify('Popup blocked — allow popups for this site, then try again', 'err');
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

function _sendToCallAnalyzer(i) {
  const s   = _scanSetups[i];
  if (!s) return;
  const sym = (s._symbol || s.symbol || '?');
  const ent = s.entry_zone || {};

  // Build rich call text with all available scanner data
  const lines = [
    `$${sym.replace('USDT','')} ${s.direction} — Setup Scanner ${s.setup_score}/10`,
    s.setup_label   ? `Grade: ${s.setup_label}`                              : '',
    s.chart_pattern ? `Pattern: ${s.chart_pattern}`                           : '',
    '',
    ent.low  ? `Entry: $${ent.low}${ent.high && ent.high !== ent.low ? ` – $${ent.high}` : ''}` : '',
    s.sl_price  ? `SL: $${s.sl_price}`   : '',
    s.tp1_price ? `TP1: $${s.tp1_price}` : '',
    s.tp2_price ? `TP2: $${s.tp2_price}` : '',
    s.rr_ratio  ? `R:R ${s.rr_ratio}`    : '',
    s.urgency   ? `Timing: ${s.urgency}` : '',
    '',
    s.why_this_score     ? `Why: ${s.why_this_score}`         : '',
    s.confluence_summary ? `Confluence: ${s.confluence_summary}` : '',
    s.summary            ? `Summary: ${s.summary}`            : '',
  ];

  // Key conditions
  const conds = s.key_conditions || [];
  if (conds.length) {
    lines.push('');
    lines.push('Signals:');
    conds.forEach(c => lines.push(`  · ${c}`));
  }

  // Risks
  const risks = s.risks || [];
  if (risks.length) {
    lines.push('');
    lines.push('Risks:');
    risks.forEach(r => lines.push(`  · ${r}`));
  }

  const text = lines.filter(l => l !== undefined && l !== null && l !== '').join('\n').trim();

  showPage('calls');
  setTimeout(() => {
    // Text
    const ta = document.getElementById('call-text');
    if (ta) { ta.value = text; ta.focus(); }

    // Analyst name
    const analystEl = document.getElementById('call-analyst');
    if (analystEl) analystEl.value = 'Setup Scanner';

    // Chart — inject scanner chart into the call analyzer image slot
    const chartB64 = s.chart_png_b64 || s._chart_png_b64 || '';
    if (chartB64) {
      // Set the global callImageB64/callImageType used by analyzeCall()
      if (typeof callImageB64 !== 'undefined') {
        callImageB64  = chartB64;
        callImageType = 'image/png';
      }
      // Show the preview image
      const preview = document.getElementById('call-img-preview');
      if (preview) {
        preview.src = 'data:image/png;base64,' + chartB64;
        preview.style.display = 'block';
      }
      // Hide the drop-zone placeholder text
      const dropZone = document.getElementById('call-img-drop');
      if (dropZone) {
        const placeholder = dropZone.querySelector('p');
        if (placeholder) placeholder.style.display = 'none';
      }
    }
  }, 200);
}

// ── Single-coin scan helpers ──────────────────────────────────────────────────

async function _doSingleScan(sym) {
  const minScore = parseInt(document.getElementById('scan-min-score')?.value || '6');
  const criteria = _readCriteriaFromCheckboxes();
  const res = await api('/api/scanner/run', 'POST',
    { force: true, symbols: [sym], min_score: minScore, criteria });
  if (!res.ok) return;
  renderScannerPage(res.data);
  if (res.data.status === 'running') _startScanPoller();
}

function _firePendingSingleScan() {
  if (!_pendingSingleScan) return;
  const sym = _pendingSingleScan;
  _pendingSingleScan = null;
  _doSingleScan(sym);
}

function _clearPendingSingleScan() {
  _pendingSingleScan = null;
  if (_scanLastState) renderScannerMeta(_scanLastState);
}

function _startSingleScan() {
  const inp = document.getElementById('scan-single-symbol');
  if (!inp) return;
  let sym = inp.value.trim().toUpperCase();
  if (!sym) return;
  if (!sym.endsWith('USDT')) sym += 'USDT';
  const state = _scanLastState || {};
  if (state.status === 'running') {
    _pendingSingleScan = sym;
    inp.value = '';
    renderScannerMeta(state);
    return;
  }
  inp.value = '';
  _doSingleScan(sym);
}

async function _loadScannerWatchlist() {
  const res = await api('/api/scanner/watchlist');
  if (res.ok) window._scannerWatchlistCount = (res.data.symbols||[]).length;
}
