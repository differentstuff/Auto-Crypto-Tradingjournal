
// ══════════════════════════════════════════════════════════════════════════════
// CHART EXPLORER
// ══════════════════════════════════════════════════════════════════════════════
let _explorerChart   = null;
let _explorerWtChart = null;
let _explorerTf      = '4H';

// Layer refs for show/hide toggles
let _explorerVolSeries = null;
let _explorerTlSeries  = [];
let _explorerSrLines   = [];
let _explorerFibLines  = [];
let _explorerLayers    = { vol: true, wt: true, sr: true, tl: true, fib: true, ind: true };

function _resetExplorerLayerRefs() {
  _explorerVolSeries = null;
  _explorerTlSeries  = [];
  _explorerSrLines   = [];
  _explorerFibLines  = [];
  _explorerLayers    = { vol: true, wt: true, sr: true, tl: true, fib: true, ind: true };
}

function _initExplorerLayerToggles() {
  let bar = document.getElementById('explorer-layer-toggles');
  if (!bar) return;
  const defs = [
    { key: 'vol', label: 'Volume' },
    { key: 'wt',  label: 'WT Pane' },
    { key: 'sr',  label: 'S/R' },
    { key: 'tl',  label: 'Trendlines' },
    { key: 'fib', label: 'Fibonacci' },
    { key: 'ind', label: 'Indicators' },
  ];
  bar.textContent = '';
  defs.forEach(({ key, label }) => {
    const btn = document.createElement('button');
    const on = _explorerLayers[key];
    btn.style.cssText = `padding:3px 10px;font-size:.74rem;border-radius:6px;cursor:pointer;border:1px solid var(--border);
      background:${on ? 'var(--accent)' : 'var(--bg2)'};color:${on ? '#fff' : 'var(--muted)'}`;
    btn.textContent = label;
    btn.onclick = () => _toggleExplorerLayer(key);
    bar.appendChild(btn);
  });
}

function _toggleExplorerLayer(name) {
  _explorerLayers[name] = !_explorerLayers[name];
  const on = _explorerLayers[name];

  if (name === 'vol' && _explorerVolSeries) {
    _explorerVolSeries.applyOptions({ visible: on });
  }
  if (name === 'wt') {
    const w = document.getElementById('explorer-wt-wrap');
    if (w) w.style.display = on ? '' : 'none';
  }
  if (name === 'sr') {
    _explorerSrLines.forEach(pl => {
      pl.applyOptions({ color: on ? pl.__col : 'rgba(0,0,0,0)', axisLabelVisible: on });
    });
  }
  if (name === 'tl') {
    _explorerTlSeries.forEach(s => s.applyOptions({ visible: on }));
  }
  if (name === 'fib') {
    _explorerFibLines.forEach(pl => {
      pl.applyOptions({ color: on ? pl.__col : 'rgba(0,0,0,0)', axisLabelVisible: on });
    });
  }
  if (name === 'ind') {
    const el = document.getElementById('explorer-indicators');
    if (el) el.style.display = on ? '' : 'none';
  }

  _initExplorerLayerToggles();
}
const _EXPLORER_TFS = ['15m', '1H', '4H', '1D'];

function _initExplorerTfBtns() {
  document.getElementById('explorer-tf-btns').innerHTML = _EXPLORER_TFS.map(tf =>
    `<button class="chart-tf-btn${tf === _explorerTf ? ' active' : ''}"
             onclick="setExplorerTf('${tf}')">${tf}</button>`
  ).join('');
}


function setExplorerTf(tf) {
  _explorerTf = tf;
  _initExplorerTfBtns();
  const sym = (document.getElementById('explorer-symbol')?.value || '').trim();
  if (sym) drawExplorerChart();
}

function explorerPopout() {
  const sym = (document.getElementById('explorer-symbol')?.value || '').trim().toUpperCase();
  if (!sym) { notify('Enter a symbol first', 'err'); return; }
  openChart(sym, _explorerTf);
}

async function drawExplorerChart() {
  const raw = (document.getElementById('explorer-symbol')?.value || '').trim().toUpperCase();
  if (!raw) { notify('Enter a symbol first', 'err'); return; }
  const sym = raw.endsWith('USDT') ? raw : raw + 'USDT';

  const wrap   = document.getElementById('explorer-chart-wrap');
  const status = document.getElementById('explorer-chart-status');
  const leg    = document.getElementById('explorer-sr-legend');
  const indEl  = document.getElementById('explorer-indicators');

  _resetExplorerLayerRefs();
  if (_explorerChart)   { _explorerChart.remove();   _explorerChart   = null; }
  if (_explorerWtChart) { _explorerWtChart.remove(); _explorerWtChart = null; }
  const oldTitle = wrap.querySelector('.explorer-chart-title');
  if (oldTitle) oldTitle.remove();
  const oldWt = document.getElementById('explorer-wt-wrap');
  if (oldWt) oldWt.remove();
  status.textContent   = 'Loading chart…';
  status.style.display = 'flex';
  leg.innerHTML        = '';
  indEl.innerHTML      = '';

  // Fetch candles + S/R + trendlines
  const res = await api(`/api/chart/candles?symbol=${sym}&timeframe=${_explorerTf}&limit=200`);
  if (!res.ok) { status.textContent = 'Error: ' + (res.error || 'failed'); return; }
  const { candles, levels, htf_levels, trendlines, fibonacci, wavetrend, current_price } = res.data;
  if (!candles || !candles.length) { status.textContent = 'No data for ' + sym; return; }

  status.style.display = 'none';

  // Title overlay — coin name + timeframe
  const titleEl = document.createElement('div');
  titleEl.className = 'explorer-chart-title';
  titleEl.style.cssText = 'position:absolute;top:10px;left:12px;z-index:4;pointer-events:none;' +
    'display:flex;align-items:baseline;gap:8px';
  const symSpan = document.createElement('span');
  symSpan.style.cssText = 'font-size:1.1rem;font-weight:700;color:var(--text);letter-spacing:.02em';
  symSpan.textContent = sym;
  const tfSpan = document.createElement('span');
  tfSpan.style.cssText = 'font-size:.78rem;font-weight:600;color:var(--accent);background:rgba(108,99,255,.15);padding:2px 8px;border-radius:4px';
  tfSpan.textContent = _explorerTf;
  titleEl.appendChild(symSpan);
  titleEl.appendChild(tfSpan);
  wrap.appendChild(titleEl);

  _explorerChart = LightweightCharts.createChart(wrap, {
    width:  wrap.clientWidth,
    height: 480,
    layout: { background: { type: 'solid', color: '#0f1117' }, textColor: '#7986cb' },
    grid:   { vertLines: { color: 'rgba(255,255,255,.04)' }, horzLines: { color: 'rgba(255,255,255,.04)' } },
    crosshair:       { mode: 1 },
    rightPriceScale: { borderColor: 'rgba(255,255,255,.1)' },
    timeScale:       { borderColor: 'rgba(255,255,255,.1)', timeVisible: true, secondsVisible: false },
  });

  const cs = _explorerChart.addCandlestickSeries({
    upColor: '#26d96b', downColor: '#ef5350',
    borderUpColor: '#26d96b', borderDownColor: '#ef5350',
    wickUpColor: '#26d96b', wickDownColor: '#ef5350',
  });
  cs.setData(candles);
  cs.priceScale().applyOptions({ scaleMargins: { top: 0.04, bottom: 0.22 } });

  // Volume histogram — bottom 20% of chart
  _explorerVolSeries = _explorerChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  const volS = _explorerVolSeries;
  _explorerChart.priceScale('vol').applyOptions({
    scaleMargins: { top: 0.82, bottom: 0 },
    visible: false,
  });
  volS.setData(candles.map(c => ({
    time:  c.time,
    value: c.volume,
    color: c.close >= c.open ? 'rgba(38,217,107,0.30)' : 'rgba(239,83,80,0.30)',
  })));

  // Axis labels for S/R (ghost line — just for right-axis price label)
  (levels || []).forEach(lvl => {
    const isS = lvl.type === 'support';
    const col = 'rgba(180,183,210,0.2)';
    const pl = cs.createPriceLine({ price: lvl.price, lineWidth: 1, lineStyle: 0, color: col, axisLabelVisible: true, title: `${isS ? 'S' : 'R'} ${lvl.touches}×` });
    pl.__col = col;
    _explorerSrLines.push(pl);
  });

  // Trendlines — lower TF first (drawn behind), higher TF last (drawn in front)
  const _TL_ALPHA = {1:.30, 2:.50, 3:.70, 4:.90};
  const _TL_WIDTH = {1:1,   2:1.5, 3:2,   4:2.5};
  [...(trendlines || [])].reverse().forEach(tl => {
    const isUp  = tl.type === 'uptrend';
    const w     = tl.weight || 1;
    const a     = _TL_ALPHA[w] ?? 0.5;
    const color = tl.at_risk
      ? `rgba(255,179,0,${Math.min(a + 0.2, 1)})`
      : (isUp ? `rgba(38,217,107,${a})` : `rgba(239,83,80,${a})`);
    const tls = _explorerChart.addLineSeries({
      color, lineWidth: _TL_WIDTH[w] ?? 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    tls.setData([
      { time: tl.p1_time, value: tl.p1_price },
      { time: tl.p2_time, value: tl.p2_price },
    ]);
    _explorerTlSeries.push(tls);
  });

  _explorerChart.timeScale().fitContent();

  // Liquidation levels from any open position on this symbol
  const liqs = (livePositionsCache || [])
    .filter(p => p.symbol === sym && p.liquidation_price)
    .map(p => ({ price: parseFloat(p.liquidation_price), label: p.direction }));

  // Axis labels for liquidations
  liqs.forEach(liq => {
    cs.createPriceLine({
      price: liq.price, lineWidth: 1, lineStyle: 0,
      color: 'rgba(255,213,60,0)', axisLabelVisible: true,
      title: `${liq.label} LIQ`,
    });
  });

  // Fibonacci levels
  if (fibonacci && fibonacci.levels) {
    fibonacci.levels.forEach(fib => {
      const col = 'rgba(180,130,255,0.55)';
      const pl = cs.createPriceLine({ price: fib.price, lineWidth: 1, lineStyle: 1, color: col, axisLabelVisible: true, title: `Fib ${fib.label}` });
      pl.__col = col;
      _explorerFibLines.push(pl);
    });
  }

  // Weekly S/R axis labels
  (htf_levels || []).forEach(lvl => {
    const isS = lvl.type === 'support';
    const col = 'rgba(255,193,60,0.3)';
    const pl = cs.createPriceLine({ price: lvl.price, lineWidth: 1, lineStyle: 0, color: col, axisLabelVisible: true, title: `1W ${isS ? 'S' : 'R'} ${lvl.touches}×` });
    pl.__col = col;
    _explorerSrLines.push(pl);
  });

  // Start canvas overlay (S/R grey boxes + liquidation dashed lines)
  _startSrOverlay(wrap, cs, levels, liqs, htf_levels);

  // Legend chips
  const chips = [];
  liqs.forEach(liq => {
    chips.push(`<span style="font-size:.72rem;padding:3px 9px;border-radius:4px;
      background:rgba(255,213,60,.1);color:rgba(255,213,60,.95);
      border:1px solid rgba(255,213,60,.2)">
      ⚡ ${liq.label} LIQ  ${liq.price}
    </span>`);
  });
  (trendlines || []).forEach(tl => {
    const isUp  = tl.type === 'uptrend';
    const tfLbl = tl.timeframe ? `<b style="opacity:.7;font-size:.63rem">${tl.timeframe}</b> ` : '';
    const risk  = tl.at_risk ? ' ⚠' : '';
    const col   = tl.at_risk ? '#ffb300' : (isUp ? 'var(--accent3)' : 'var(--red)');
    const bg    = tl.at_risk ? 'rgba(255,179,0,.10)' : (isUp ? 'rgba(38,217,107,.07)' : 'rgba(239,83,80,.07)');
    const bdr   = tl.at_risk ? 'rgba(255,179,0,.30)'  : (isUp ? 'rgba(38,217,107,.18)' : 'rgba(239,83,80,.18)');
    chips.push(`<span style="font-size:.72rem;padding:3px 9px;border-radius:4px;
      background:${bg};color:${col};border:1px solid ${bdr}"
      title="${_esc(tl.anchor1)} → ${_esc(tl.anchor2)}${tl.at_risk ? ' — nearly breached' : ''}">
      ${tfLbl}${isUp ? '↗ Up' : '↘ Down'} TL (${tl.touches}×)${risk}
    </span>`);
  });
  (htf_levels || []).forEach(lvl => {
    const isS  = lvl.type === 'support';
    const dist = current_price ? ((lvl.price - current_price) / current_price * 100).toFixed(1) : null;
    const dtxt = dist !== null ? ` · ${dist > 0 ? '+' : ''}${dist}%` : '';
    chips.push(`<span style="font-size:.72rem;padding:3px 9px;border-radius:4px;
      background:rgba(255,193,60,.10);color:rgba(255,193,60,.95);
      border:1px solid rgba(255,193,60,.25)" title="Weekly timeframe structural level">
      1W ${isS ? '▲ S' : '▼ R'} ${lvl.price}${dtxt} (${lvl.touches}×)
    </span>`);
  });
  [...(levels || [])].reverse().forEach(lvl => {
    const isS  = lvl.type === 'support';
    const dist = current_price ? ((lvl.price - current_price) / current_price * 100).toFixed(1) : null;
    const dtxt = dist !== null ? ` · ${dist > 0 ? '+' : ''}${dist}%` : '';
    chips.push(`<span style="font-size:.72rem;padding:3px 9px;border-radius:4px;
      background:rgba(180,183,210,.08);color:${isS ? '#c8cade' : '#a0a8cc'}">
      ${isS ? '▲ S' : '▼ R'} ${lvl.price}${dtxt} (${lvl.touches}×)
    </span>`);
  });
  leg.innerHTML = chips.length ? chips.join('') : '<span style="color:var(--muted);font-size:.74rem">No S/R or trendlines detected</span>'; // safe: all values are escaped prices/numbers

  // VMC Cipher B — WaveTrend pane (inserted dynamically between chart and legend)
  _buildExplorerWtPane(wrap, wavetrend || [], _explorerChart);

  // Resize handler
  window.__explorerResize = () => {
    if (_explorerChart) _explorerChart.applyOptions({ width: wrap.clientWidth });
  };
  window.removeEventListener('resize', window.__explorerResize);
  window.addEventListener('resize',    window.__explorerResize);

  // Show layer toggle bar and load indicators
  _initExplorerLayerToggles();
  _loadExplorerIndicators(sym);
}

async function _loadExplorerIndicators(sym) {
  const el  = document.getElementById('explorer-indicators');
  const res = await api(`/api/chart/indicators?symbol=${sym}&timeframes=${_explorerTf}`);
  if (!res.ok) return;
  const tf  = res.data[_explorerTf];
  if (!tf || !tf.indicators || !tf.indicators.ok) return;
  const ind = tf.indicators;

  el.textContent = '';

  // Compact 2-column row builder
  const rows = [];
  const row = (label, value, color = 'var(--text)', sub = '') => {
    rows.push({ label, value, color, sub });
  };

  if (ind.rsi) {
    const c = ind.rsi.value > 70 ? 'var(--red)' : ind.rsi.value < 30 ? 'var(--accent3)' : 'var(--text)';
    row('RSI 14', ind.rsi.value, c, ind.rsi.signal);
  }
  if (ind.macd) {
    const c = ind.macd.trend === 'bullish' ? 'var(--accent3)' : 'var(--red)';
    const cross = ind.macd.crossover ? ' · crossover' : ind.macd.crossunder ? ' · crossunder' : '';
    row('MACD', ind.macd.trend.toUpperCase(), c, ind.macd.histogram_trend + cross);
  }
  if (ind.ema) {
    const bullish = ind.ema.alignment?.startsWith('fully bullish');
    const bearish = ind.ema.alignment?.startsWith('fully bearish');
    const c = bullish ? 'var(--accent3)' : bearish ? 'var(--red)' : 'var(--yellow)';
    row('EMA Stack', ind.ema.stack || 'Mixed', c, ind.ema.alignment || '');
  }
  if (ind.bollinger) {
    const c = ind.bollinger.position_pct > 80 ? 'var(--red)' : ind.bollinger.position_pct < 20 ? 'var(--accent3)' : 'var(--text)';
    row('Bollinger', `${ind.bollinger.position_pct}th %ile`, c, ind.bollinger.signal);
  }
  if (ind.adx) {
    const c = ind.adx.value > 25 ? 'var(--yellow)' : 'var(--muted)';
    row('ADX 14', ind.adx.value, c, `${ind.adx.strength}${ind.adx.direction ? ' · ' + ind.adx.direction : ''}`);
  }
  if (ind.stoch_rsi) {
    const c = ind.stoch_rsi.k > 80 ? 'var(--red)' : ind.stoch_rsi.k < 20 ? 'var(--accent3)' : 'var(--text)';
    row('Stoch RSI', `K ${ind.stoch_rsi.k} / D ${ind.stoch_rsi.d}`, c, ind.stoch_rsi.signal);
  }
  if (ind.atr) {
    row('ATR 14', ind.atr.value, 'var(--muted)', `${ind.atr.pct}% of price`);
  }
  if (ind.volume) {
    const c = ind.volume.ratio > 1.5 ? 'var(--yellow)' : ind.volume.ratio < 0.7 ? 'var(--muted)' : 'var(--text)';
    row('Volume', `${ind.volume.ratio}× avg`, c, ind.volume.signal);
  }
  if (ind.wavetrend) {
    const wt = ind.wavetrend;
    const sig = wt.signal;
    const sigLabel = sig === 'gold_buy' ? '🟡 Gold' : sig === 'buy' ? '🟢 Buy' : sig === 'sell' ? '🔴 Sell' : '—';
    const c = wt.wt1 > 53 ? 'var(--red)' : wt.wt1 < -53 ? 'var(--accent3)' : 'var(--text)';
    row('WT1 / WT2', `${wt.wt1} / ${wt.wt2}`, c, `MFI ${wt.mfi} · ${wt.zone} · ${sigLabel}`);
  }
  if (ind.support_resistance && ind.support_resistance.length) {
    const sups = ind.support_resistance.filter(l => l.type === 'support').sort((a,b) => b.price - a.price);
    const ress = ind.support_resistance.filter(l => l.type === 'resistance').sort((a,b) => a.price - b.price);
    if (sups[0]) row('Support', `${sups[0].price}`, 'var(--accent3)', `${sups[0].touches}× touches`);
    if (ress[0]) row('Resistance', `${ress[0].price}`, 'var(--red)', `${ress[0].touches}× touches`);
  }

  // Render as compact 2-column table
  const wrap = document.createElement('div');
  wrap.style.cssText = 'background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden';
  rows.forEach((r, i) => {
    const line = document.createElement('div');
    line.style.cssText = `display:flex;align-items:center;padding:7px 14px;gap:10px;` +
      (i < rows.length - 1 ? 'border-bottom:1px solid rgba(255,255,255,.04)' : '');
    if (i % 2 === 1) line.style.background = 'rgba(255,255,255,.02)';

    const lbl = document.createElement('span');
    lbl.style.cssText = 'font-size:.73rem;color:var(--muted);width:80px;flex-shrink:0';
    lbl.textContent = r.label;

    const val = document.createElement('span');
    val.style.cssText = `font-size:.82rem;font-weight:600;color:${r.color};flex-shrink:0;width:110px`;
    val.textContent = String(r.value);

    const sub = document.createElement('span');
    sub.style.cssText = 'font-size:.72rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    sub.textContent = r.sub || '';

    line.appendChild(lbl);
    line.appendChild(val);
    line.appendChild(sub);
    wrap.appendChild(line);
  });

  el.appendChild(wrap);
}

// ── VMC Cipher B WaveTrend pane for Chart Explorer ────────────────────────────
function _buildExplorerWtPane(chartWrap, wtData, mainChart) {
  if (_explorerWtChart) { _explorerWtChart.remove(); _explorerWtChart = null; }
  if (!wtData || !wtData.length) return;

  // Create wt-wrap div and insert after the chart wrap's parent
  const wtWrap = document.createElement('div');
  wtWrap.id = 'explorer-wt-wrap';
  wtWrap.style.cssText = 'height:130px;position:relative;border-top:1px solid rgba(255,255,255,.06);background:#0a0d14';
  chartWrap.parentNode.insertBefore(wtWrap, chartWrap.nextSibling);

  const label = document.createElement('div');
  label.style.cssText = 'position:absolute;top:4px;left:8px;z-index:4;pointer-events:none;font-size:.68rem;font-weight:600;letter-spacing:.04em;color:rgba(121,134,203,0.6)';
  label.textContent = 'VMC Cipher B — WaveTrend';
  wtWrap.appendChild(label);

  _explorerWtChart = LightweightCharts.createChart(wtWrap, {
    width:  wtWrap.clientWidth,
    height: 130,
    layout: { background: { type: 'solid', color: '#0a0d14' }, textColor: 'rgba(121,134,203,0.6)' },
    grid:   { vertLines: { color: 'rgba(255,255,255,.03)' }, horzLines: { color: 'rgba(255,255,255,.03)' } },
    crosshair:       { mode: 1 },
    rightPriceScale: { borderColor: 'rgba(255,255,255,.07)', scaleMargins: { top: 0.05, bottom: 0.05 } },
    timeScale:       { visible: false },
    handleScroll:    false,
    handleScale:     false,
  });

  // WT2 (red), reference lines, MFI histogram, WT1 (teal), markers — same as chart.html
  const wt2S = _explorerWtChart.addLineSeries({
    color: 'rgba(239,83,80,0.70)', lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  wt2S.setData(wtData.map(d => ({ time: d.time, value: d.wt2 })));
  [{ p: 60, c: 'rgba(239,83,80,0.20)', t: 'OB' },
   { p: 53, c: 'rgba(239,83,80,0.10)', t: '' },
   { p:  0, c: 'rgba(255,255,255,0.12)', t: '0' },
   { p:-53, c: 'rgba(38,217,107,0.10)', t: '' },
   { p:-60, c: 'rgba(38,217,107,0.20)', t: 'OS' },
   { p:-80, c: 'rgba(255,213,60,0.25)', t: 'GOLD' },
  ].forEach(r => wt2S.createPriceLine({ price: r.p, lineWidth: 1, lineStyle: 2, color: r.c, axisLabelVisible: !!r.t, title: r.t }));

  const mfiS = _explorerWtChart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'mfi' });
  _explorerWtChart.priceScale('mfi').applyOptions({ scaleMargins: { top: 0.60, bottom: 0 }, visible: false });
  mfiS.setData(wtData.map(d => ({ time: d.time, value: Math.abs(d.mfi), color: d.mfi >= 0 ? 'rgba(38,217,107,0.18)' : 'rgba(239,83,80,0.18)' })));

  const wt1S = _explorerWtChart.addLineSeries({
    color: 'rgba(79,195,247,0.90)', lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true,
  });
  wt1S.setData(wtData.map(d => ({ time: d.time, value: d.wt1 })));
  wt1S.setMarkers(wtData.filter(d => d.signal).map(d => ({
    time: d.time,
    position: d.signal === 'sell' ? 'aboveBar' : 'belowBar',
    color: d.signal === 'gold_buy' ? '#ffd700' : d.signal === 'buy' ? '#26d96b' : '#ef5350',
    shape: d.signal === 'sell' ? 'arrowDown' : 'circle',
    size: d.signal === 'gold_buy' ? 2 : 1,
  })));

  // Sync timeScales
  mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range && _explorerWtChart) _explorerWtChart.timeScale().setVisibleLogicalRange(range);
  });
  _explorerWtChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range && _explorerChart) _explorerChart.timeScale().setVisibleLogicalRange(range);
  });

  // Keep wt-wrap width in sync on resize
  window.__explorerWtResize = () => {
    if (_explorerWtChart) _explorerWtChart.applyOptions({ width: wtWrap.clientWidth });
  };
  window.removeEventListener('resize', window.__explorerWtResize);
  window.addEventListener('resize', window.__explorerWtResize);
}

