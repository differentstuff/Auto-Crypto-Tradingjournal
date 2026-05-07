// ── State ──────────────────────────────────────────────────────────────────────
const charts = {};
let currentPage = 'dashboard';

// ── S/R Chart — opens as a detached, resizable window ───────────────────────
function openChart(symbol, tf = '4H') {
  // Attach any open-position liquidation levels so the popup can draw them
  const liqs = (livePositionsCache || [])
    .filter(p => p.symbol === symbol && p.liquidation_price)
    .map(p => ({ price: parseFloat(p.liquidation_price), label: p.direction }));

  let url = `/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`;
  if (liqs.length) url += '&liqs=' + encodeURIComponent(JSON.stringify(liqs));

  window.open(url, `chart_${symbol}`,
    'width=1060,height=680,resizable=yes,scrollbars=no,toolbar=no,menubar=no,location=no');
}

// ── Canvas overlay: S/R grey boxes + liquidation dashed lines ────────────────
// Shared by Chart Explorer (inline) and any future inline charts.
function _startSrOverlay(wrap, series, levels, liquidations) {
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
const _tip = document.createElement('div');
_tip.id = 'tip';
document.body.appendChild(_tip);
document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-tip]');
  if (!el) { _tip.classList.remove('visible'); return; }
  _tip.textContent = el.dataset.tip;
  _tip.classList.add('visible');
});
document.addEventListener('mouseout', e => {
  if (e.target.closest('[data-tip]')) _tip.classList.remove('visible');
});
document.addEventListener('mousemove', e => {
  if (!_tip.classList.contains('visible')) return;
  const x = e.clientX + 14, y = e.clientY + 14;
  _tip.style.left = (x + _tip.offsetWidth  > window.innerWidth  ? e.clientX - _tip.offsetWidth  - 8 : x) + 'px';
  _tip.style.top  = (y + _tip.offsetHeight > window.innerHeight ? e.clientY - _tip.offsetHeight - 8 : y) + 'px';
});
// ── end tooltip ───────────────────────────────────────────────────────────────

let journalPage = 1;
let symbolList  = [];
let _exchangeSymbols = [];  // all USDT-M futures symbols from Bitget

// ── Symbol Picker ─────────────────────────────────────────────────────────────
function _hlMatch(str, q) {
  if (!q) return str;
  const i = str.toUpperCase().indexOf(q.toUpperCase());
  if (i < 0) return str;
  return str.slice(0, i) + '<b>' + str.slice(i, i + q.length) + '</b>' + str.slice(i + q.length);
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
      ? hits.map(s => `<div class="sym-opt" data-v="${s}">${_hlMatch(s, q)}</div>`).join('')
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
  return r.json();
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

