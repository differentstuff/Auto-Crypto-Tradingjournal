// ── State ──────────────────────────────────────────────────────────────────────
const charts = {};
let currentPage = 'dashboard';

// ── HTML escape helper (XSS guard) ────────────────────────────────────────────
function _esc(s) {
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Notification toast ────────────────────────────────────────────────────────
let _notifyTimer = null;
function notify(msg, type) {
  let bar = document.getElementById('_notify_bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = '_notify_bar';
    bar.style.cssText = [
      'position:fixed;bottom:24px;left:50%;transform:translateX(-50%)',
      'padding:10px 20px;border-radius:8px;font-size:.85rem;font-weight:600',
      'z-index:99999;pointer-events:none;transition:opacity .3s',
      'box-shadow:0 4px 16px rgba(0,0,0,.4)',
    ].join(';');
    document.body.appendChild(bar);
  }
  bar.textContent = msg;
  bar.style.opacity = '1';
  if (type === 'err') {
    bar.style.background = 'rgba(239,83,80,.9)';
    bar.style.color = '#fff';
  } else {
    bar.style.background = 'rgba(38,217,107,.9)';
    bar.style.color = '#000';
  }
  clearTimeout(_notifyTimer);
  _notifyTimer = setTimeout(() => { bar.style.opacity = '0'; }, 3000);
}

// ── Global exchange filter ────────────────────────────────────────────────────
// 'all' | 'bitget' | 'blofin' — persisted in localStorage, applied to every
// stats/analytics API call so every page shows data for the selected exchange.
let _globalExchange = localStorage.getItem('globalExchange') || 'all';

function setGlobalExchange(val, btn) {
  _globalExchange = val;
  localStorage.setItem('globalExchange', val);
  // Update pill styles
  ['all','bitget','blofin'].forEach(id => {
    const el = document.getElementById('ep-' + id);
    if (el) el.classList.toggle('active', id === val);
  });
  // Reload whatever page is currently visible
  _reloadCurrentPage();
}

/** Return the exchange param string for appending to API calls, e.g. '&exchange=bitget' or '' */
function exchParam() {
  return _globalExchange && _globalExchange !== 'all' ? `&exchange=${_globalExchange}` : '';
}

/** Return exchange as a filters object key (for POST bodies) */
function exchFilters() {
  return _globalExchange && _globalExchange !== 'all' ? { exchange: _globalExchange } : {};
}

function _reloadCurrentPage() {
  switch(currentPage) {
    case 'dashboard': if (typeof loadDashboard  === 'function') loadDashboard();  break;
    case 'journal':   if (typeof journalLoad    === 'function') journalLoad(1);   break;
    case 'deep':      if (typeof loadDeepStats  === 'function') loadDeepStats();  break;
    case 'edge':      if (typeof loadEdgeLab    === 'function') loadEdgeLab();    break;
    case 'ai':        if (typeof loadAdvisor    === 'function') loadAdvisor();    break;
    case 'hindsight': if (typeof loadHindsight    === 'function') loadHindsight();    break;
    case 'import':    if (typeof loadImportStatus === 'function') loadImportStatus(); break;
    case 'trades':    if (typeof loadLiveTrades   === 'function') loadLiveTrades();   break;
  }
}

// ── S/R Chart — opens as a detached, resizable window ───────────────────────
function openChart(symbol, tf = '4H') {
  if (!symbol) { notify('No symbol to chart', 'err'); return; }

  const liqs = (livePositionsCache || [])
    .filter(p => p.symbol === symbol && p.liquidation_price)
    .map(p => ({ price: parseFloat(p.liquidation_price), label: p.direction }));

  // Entry / SL / TP levels from any open position on this symbol
  const trades = (livePositionsCache || [])
    .filter(p => p.symbol === symbol)
    .map(p => {
      const key  = p.symbol + '_' + p.direction;
      const call = typeof liveCallMatches !== 'undefined' ? (liveCallMatches[key] || null) : null;
      return {
        dir:   p.direction,
        entry: parseFloat(p.entry_price)  || null,
        sl:    parseFloat(p.stop_loss)    || null,
        tp1:   parseFloat(p.take_profit)  || (call ? parseFloat(call.tp1_price) || null : null),
        tp2:   call ? parseFloat(call.tp2_price) || null : null,
      };
    })
    .filter(t => t.entry);

  let url = `/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`;
  if (liqs.length)   url += '&liqs='   + encodeURIComponent(JSON.stringify(liqs));
  if (trades.length) url += '&trades=' + encodeURIComponent(JSON.stringify(trades));

  const w = window.open(url, `chart_${symbol}`,
    'width=1060,height=680,resizable=yes,scrollbars=no,toolbar=no,menubar=no,location=no');
  if (!w) notify('Popup blocked — allow popups for this site, then try again', 'err');
}

// ── Canvas overlay: S/R grey boxes + liquidation dashed lines ────────────────
// Shared by Chart Explorer (inline) and any future inline charts.
function _startSrOverlay(wrap, series, levels, liquidations, htf_levels) {
  const old = wrap.querySelector('.sr-canvas');
  if (old) old.remove();

  const cv = document.createElement('canvas');
  cv.className = 'sr-canvas';
  cv.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:3';
  wrap.appendChild(cv);

  function draw() {
    if (!document.contains(cv)) return;
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    const ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    const cw = w - 65; // leave price-axis column uncovered

    // Weekly S/R — gold/amber, drawn first (behind current-TF levels)
    (htf_levels || []).forEach(lvl => {
      const hw = lvl.price * 0.004;
      const yT = series.priceToCoordinate(lvl.price + hw);
      const yB = series.priceToCoordinate(lvl.price - hw);
      if (yT === null || yB === null) return;
      const a = Math.min(0.10 + (lvl.touches - 1) * 0.04, 0.45).toFixed(3);
      ctx.fillStyle = `rgba(255,193,60,${a})`;
      const top = Math.min(yT, yB);
      ctx.fillRect(0, top, cw, Math.max(Math.abs(yB - yT), 4));
    });

    (levels || []).forEach(lvl => {
      const hw = lvl.price * 0.003;
      const yT = series.priceToCoordinate(lvl.price + hw);
      const yB = series.priceToCoordinate(lvl.price - hw);
      if (yT === null || yB === null) return;
      const a   = Math.min(0.07 + (lvl.touches - 1) * 0.035, 0.42).toFixed(3);
      ctx.fillStyle = `rgba(180,183,210,${a})`;
      const top = Math.min(yT, yB);
      ctx.fillRect(0, top, cw, Math.max(Math.abs(yB - yT), 3));
    });

    (liquidations || []).forEach(liq => {
      if (!liq.price) return;
      const y = series.priceToCoordinate(parseFloat(liq.price));
      if (y === null) return;
      ctx.save();
      ctx.strokeStyle = 'rgba(255,213,60,0.9)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([7, 4]);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cw, y); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = 'rgba(255,213,60,0.92)';
      ctx.font = 'bold 11px "Segoe UI",sans-serif';
      ctx.fillText(`${liq.label} LIQ  ${liq.price}`, 8, y - 5);
    });

    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}
// ── end chart ────────────────────────────────────────────────────────────────

// ── Tooltip engine ────────────────────────────────────────────────────────────
// ── Tooltip — anchored popup above the element, not cursor-following ──────────
const _tip = document.createElement('div');
_tip.id = 'tip';
document.body.appendChild(_tip);

function _positionTip(el) {
  const r   = el.getBoundingClientRect();
  const tw  = _tip.offsetWidth  || 260;
  const th  = _tip.offsetHeight || 40;
  const gap = 8;

  // Horizontal: centre over element, clamp to viewport
  let left = r.left + r.width / 2 - tw / 2;
  left = Math.max(gap, Math.min(left, window.innerWidth - tw - gap));

  // Vertical: prefer above; flip below if too close to top
  let top;
  if (r.top - th - gap > 0) {
    top = r.top - th - gap + window.scrollY;       // above
    _tip.classList.remove('tip-below');
  } else {
    top = r.bottom + gap + window.scrollY;          // below
    _tip.classList.add('tip-below');
  }

  _tip.style.left = left + 'px';
  _tip.style.top  = top  + 'px';
}

document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-tip]');
  if (!el) { _tip.classList.remove('visible'); return; }
  _tip.textContent = el.dataset.tip;
  _tip.classList.add('visible');
  _positionTip(el);
});
document.addEventListener('mouseout', e => {
  if (e.target.closest('[data-tip]')) _tip.classList.remove('visible');
});
// ── end tooltip ───────────────────────────────────────────────────────────────

let journalPage = 1;
let symbolList  = [];
let _exchangeSymbols = [];  // all USDT-M futures symbols from Bitget

// ── Symbol Picker ─────────────────────────────────────────────────────────────
function _hlMatch(str, q) {
  if (!q) return _esc(str);
  const i = str.toUpperCase().indexOf(q.toUpperCase());
  if (i < 0) return _esc(str);
  return _esc(str.slice(0, i)) + '<b>' + _esc(str.slice(i, i + q.length)) + '</b>' + _esc(str.slice(i + q.length));
}

function _attachSymbolPicker(inputId) {
  const inp = document.getElementById(inputId);
  if (!inp || inp._symPicker) return;
  inp._symPicker = true;
  inp.removeAttribute('list');
  inp.setAttribute('autocomplete', 'off');

  // Inputs inside a .modal need position:fixed to escape overflow-y:auto clipping.
  // Everything else (Chart Explorer etc.) uses a wrapper + position:absolute.
  const inModal = !!inp.closest('.modal');

  const drop = document.createElement('div');
  drop.className = 'sym-drop' + (inModal ? ' sym-drop-fixed' : ' sym-drop-abs');

  if (inModal) {
    document.body.appendChild(drop);
  } else {
    // Wrap the input so the dropdown can be positioned absolute relative to it
    const wrap = document.createElement('div');
    wrap.className = 'sym-wrap';
    inp.parentElement.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    wrap.appendChild(drop);
  }

  // For fixed-position dropdowns: recompute position on each open
  function _pos() {
    if (!inModal) return;
    const r = inp.getBoundingClientRect();
    drop.style.top   = (r.bottom + 3) + 'px';
    drop.style.left  = r.left         + 'px';
    drop.style.width = Math.max(r.width, 220) + 'px';
  }

  function _list() {
    return _exchangeSymbols.length ? _exchangeSymbols : symbolList;
  }

  function _render(q) {
    const src = _list();
    const hits = src.length < 1 ? [] :
      (q.length < 1 ? src.slice(0, 80)
                    : src.filter(s => s.toUpperCase().includes(q.toUpperCase())).slice(0, 100));
    drop.innerHTML = hits.length
      ? hits.map(s => `<div class="sym-opt" data-v="${_esc(s)}">${_hlMatch(s, q)}</div>`).join('')
      : `<div class="sym-no-match">${src.length ? 'No matches' : 'Loading…'}</div>`;
    drop.querySelectorAll('.sym-opt').forEach(el =>
      el.addEventListener('mousedown', e => {
        e.preventDefault();
        inp.value = el.dataset.v;
        drop.classList.remove('open');
        inp.dispatchEvent(new Event('change'));
      })
    );
  }
  // Store render + input refs so _loadExchangeSymbols can update open dropdowns
  drop._render = _render;
  drop._inp    = inp;

  inp.addEventListener('focus', () => { _pos(); _render(inp.value); drop.classList.add('open'); });
  inp.addEventListener('input', () => { _pos(); _render(inp.value); drop.classList.add('open'); });
  inp.addEventListener('blur',  () => setTimeout(() => drop.classList.remove('open'), 160));
  inp.addEventListener('keydown', e => {
    if (e.key === 'Escape') { drop.classList.remove('open'); return; }
    const opts = [...drop.querySelectorAll('.sym-opt')];
    const cur  = drop.querySelector('.sym-opt.hi');
    let idx    = cur ? opts.indexOf(cur) : -1;
    if (e.key === 'ArrowDown')    idx = Math.min(idx + 1, opts.length - 1);
    else if (e.key === 'ArrowUp') idx = Math.max(idx - 1, 0);
    else if (e.key === 'Enter' && cur) {
      inp.value = cur.dataset.v;
      drop.classList.remove('open');
      inp.dispatchEvent(new Event('change'));
      e.preventDefault(); e.stopPropagation(); return;
    } else return;
    opts.forEach(o => o.classList.remove('hi'));
    opts[idx]?.classList.add('hi');
    opts[idx]?.scrollIntoView({ block: 'nearest' });
    e.preventDefault();
  });
}

async function _loadExchangeSymbols() {
  const r = await api('/api/exchange/symbols');
  if (r.ok && r.data.length) {
    _exchangeSymbols = r.data;
    // Refresh any currently open picker so it shows the full list
    document.querySelectorAll('.sym-drop.open').forEach(d => {
      if (d._render && d._inp) d._render(d._inp.value);
    });
  }
}

// ── Navigation ─────────────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page-view').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  currentPage = name;
  if (name === 'dashboard') loadDashboard();
  if (name === 'journal')   { loadSymbols(); journalLoad(1); }
  if (name === 'deep')      loadDeep();
  if (name === 'edge')      loadEdge();
  if (name === 'import')    loadImportLog();
}

// ── Helpers ────────────────────────────────────────────────────────────────────
const fmt   = v => v == null ? '—' : Number(v).toFixed(4);
const fmtC  = v => v == null ? '—' : Number(v).toFixed(2);
const pnlClass = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const pnlSign  = v => v > 0 ? '+' : '';
function durFmt(mins) {
  if (mins == null) return '—';
  if (mins < 60) return mins + 'm';
  if (mins < 1440) return Math.round(mins/60) + 'h ' + (mins%60) + 'm';
  return Math.floor(mins/1440) + 'd ' + Math.floor((mins%1440)/60) + 'h';
}
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  const text = await r.text();
  try {
    return JSON.parse(text);
  } catch {
    return { ok: false, error: `Server error (HTTP ${r.status})` };
  }
}

// ── Chart helpers ─────────────────────────────────────────────────────────────
const chartDefaults = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { labels: { color: '#7986cb', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#7986cb', font: { size: 10 } }, grid: { color: 'rgba(45,50,80,.6)' } },
    y: { ticks: { color: '#7986cb', font: { size: 10 } }, grid: { color: 'rgba(45,50,80,.6)' } }
  }
};
function makeChart(id, type, data, extraOpts={}) {
  if (charts[id]) charts[id].destroy();
  const ctx = document.getElementById(id).getContext('2d');
  charts[id] = new Chart(ctx, { type, data,
    options: Object.assign({}, chartDefaults, extraOpts) });
}

// KPI click-to-info popup — attaches once on DOMContentLoaded.
// Any element with [data-tip] gets a clickable info panel below it.
(function() {
  let _openPopup = null;

  function _closeOpen() {
    if (_openPopup) { _openPopup.remove(); _openPopup = null; }
  }

  document.addEventListener('click', function(e) {
    // Close if clicking outside any popup or tip target
    if (_openPopup && !_openPopup.contains(e.target)) {
      const host = _openPopup._host;
      if (!host || !host.contains(e.target)) { _closeOpen(); return; }
    }

    const el = e.target.closest('[data-tip]');
    if (!el) return;

    // Toggle: clicking the same card closes it
    if (_openPopup && _openPopup._host === el) { _closeOpen(); return; }
    _closeOpen();

    const tip = el.getAttribute('data-tip');
    if (!tip) return;
    e.stopPropagation();

    const popup = document.createElement('div');
    popup.className = 'kpi-tip-popup';
    popup._host = el;

    const closeX = document.createElement('span');
    closeX.textContent = '✕';
    closeX.style.cssText = 'float:right;cursor:pointer;opacity:.6;margin-left:8px;font-size:.9em';
    closeX.onclick = _closeOpen;

    const body = document.createElement('span');
    body.textContent = tip;

    popup.appendChild(closeX);
    popup.appendChild(body);

    // Insert after the host element
    el.style.position = 'relative';
    el.insertAdjacentElement('afterend', popup);
    _openPopup = popup;
  }, true);
})();

// Lightweight markdown → HTML for AI text fields.
// Escapes HTML first, then applies inline formatting and list detection.
function mdToHtml(text) {
  if (!text) return '';

  function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function fmt(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*\n]+?)\*/g, '<em>$1</em>')
      .replace(/`([^`\n]+)`/g, '<code style="background:var(--border);padding:1px 4px;border-radius:3px;font-family:monospace;font-size:.85em">$1</code>');
  }

  const lines = text.split('\n');
  const parts = [];
  let inUl = false, inOl = false, prevBlank = false;

  for (const raw of lines) {
    const line = raw.trim();
    const blank = line === '';
    const ulM   = !blank && line.match(/^[-*•] (.+)/);
    const olM   = !blank && line.match(/^\d+[.)]\s+(.+)/);

    if (ulM) {
      if (inOl) { parts.push('</ol>'); inOl = false; }
      if (!inUl) { parts.push('<ul style="margin:4px 0 4px 18px;padding:0;list-style:disc">'); inUl = true; }
      parts.push(`<li style="margin-bottom:2px">${fmt(ulM[1])}</li>`);
      prevBlank = false;
    } else if (olM) {
      if (inUl) { parts.push('</ul>'); inUl = false; }
      if (!inOl) { parts.push('<ol style="margin:4px 0 4px 18px;padding:0">'); inOl = true; }
      parts.push(`<li style="margin-bottom:2px">${fmt(olM[1])}</li>`);
      prevBlank = false;
    } else if (blank) {
      if (inUl) { parts.push('</ul>'); inUl = false; }
      if (inOl) { parts.push('</ol>'); inOl = false; }
      if (!prevBlank) parts.push('<br>');
      prevBlank = true;
    } else {
      if (inUl) { parts.push('</ul>'); inUl = false; }
      if (inOl) { parts.push('</ol>'); inOl = false; }
      if (parts.length && !prevBlank) parts.push('<br>');
      parts.push(fmt(line));
      prevBlank = false;
    }
  }
  if (inUl) parts.push('</ul>');
  if (inOl) parts.push('</ol>');
  while (parts.length && parts[0] === '<br>') parts.shift();
  while (parts.length && parts[parts.length-1] === '<br>') parts.pop();
  return parts.join('');
}

