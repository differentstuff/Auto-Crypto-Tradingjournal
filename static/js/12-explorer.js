
// ══════════════════════════════════════════════════════════════════════════════
// CHART EXPLORER
// ══════════════════════════════════════════════════════════════════════════════
let _explorerChart = null;
let _explorerTf    = '4H';
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
  if (!sym) return;
  openChart(sym, _explorerTf);
}

async function drawExplorerChart() {
  const raw = (document.getElementById('explorer-symbol')?.value || '').trim().toUpperCase();
  if (!raw) return;
  const sym = raw.endsWith('USDT') ? raw : raw + 'USDT';

  const wrap   = document.getElementById('explorer-chart-wrap');
  const status = document.getElementById('explorer-chart-status');
  const leg    = document.getElementById('explorer-sr-legend');
  const indEl  = document.getElementById('explorer-indicators');

  if (_explorerChart) { _explorerChart.remove(); _explorerChart = null; }
  const oldTitle = wrap.querySelector('.explorer-chart-title');
  if (oldTitle) oldTitle.remove();
  status.textContent   = 'Loading chart…';
  status.style.display = 'flex';
  leg.innerHTML        = '';
  indEl.innerHTML      = '';

  // Fetch candles + S/R + trendlines
  const res = await api(`/api/chart/candles?symbol=${sym}&timeframe=${_explorerTf}&limit=200`);
  if (!res.ok) { status.textContent = 'Error: ' + (res.error || 'failed'); return; }
  const { candles, levels, trendlines, current_price } = res.data;
  if (!candles || !candles.length) { status.textContent = 'No data for ' + sym; return; }

  status.style.display = 'none';

  // Title overlay — coin name + timeframe
  const titleEl = document.createElement('div');
  titleEl.className = 'explorer-chart-title';
  titleEl.style.cssText = 'position:absolute;top:10px;left:12px;z-index:4;pointer-events:none;' +
    'display:flex;align-items:baseline;gap:8px';
  titleEl.innerHTML =
    `<span style="font-size:1.1rem;font-weight:700;color:var(--text);letter-spacing:.02em">${sym}</span>` +
    `<span style="font-size:.78rem;font-weight:600;color:var(--accent);background:rgba(108,99,255,.15);` +
    `padding:2px 8px;border-radius:4px">${_explorerTf}</span>`;
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

  // Axis labels for S/R (ghost line — just for right-axis price label)
  (levels || []).forEach(lvl => {
    const isS = lvl.type === 'support';
    cs.createPriceLine({
      price: lvl.price, lineWidth: 1, lineStyle: 0,
      color: 'rgba(180,183,210,0.2)', axisLabelVisible: true,
      title: `${isS ? 'S' : 'R'} ${lvl.touches}×`,
    });
  });

  // Trendlines — lower TF first (drawn behind), higher TF last (drawn in front)
  const _TL_ALPHA = {1:.30, 2:.50, 3:.70, 4:.90};
  const _TL_WIDTH = {1:1,   2:1.5, 3:2,   4:2.5};
  [...(trendlines || [])].reverse().forEach(tl => {
    const isUp = tl.type === 'uptrend';
    const w    = tl.weight || 1;
    const tls  = _explorerChart.addLineSeries({
      color:     isUp ? `rgba(38,217,107,${_TL_ALPHA[w]??0.5})` : `rgba(239,83,80,${_TL_ALPHA[w]??0.5})`,
      lineWidth: _TL_WIDTH[w] ?? 1,
      lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    tls.setData([
      { time: tl.p1_time, value: tl.p1_price },
      { time: tl.p2_time, value: tl.p2_price },
    ]);
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

  // Start canvas overlay (S/R grey boxes + liquidation dashed lines)
  _startSrOverlay(wrap, cs, levels, liqs);

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
    const isUp = tl.type === 'uptrend';
    const tf   = tl.timeframe ? `<b style="opacity:.7;font-size:.63rem">${tl.timeframe}</b> ` : '';
    chips.push(`<span style="font-size:.72rem;padding:3px 9px;border-radius:4px;
      background:${isUp ? 'rgba(38,217,107,.07)' : 'rgba(239,83,80,.07)'};
      color:${isUp ? 'var(--accent3)' : 'var(--red)'};
      border:1px solid ${isUp ? 'rgba(38,217,107,.18)' : 'rgba(239,83,80,.18)'}">
      ${tf}${isUp ? '↗ Up' : '↘ Down'} TL (${tl.touches}×)
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
  leg.innerHTML = chips.length ? chips.join('') : '<span style="color:var(--muted);font-size:.74rem">No S/R or trendlines detected</span>';

  // Resize handler
  window.__explorerResize = () => {
    if (_explorerChart) _explorerChart.applyOptions({ width: wrap.clientWidth });
  };
  window.removeEventListener('resize', window.__explorerResize);
  window.addEventListener('resize',    window.__explorerResize);

  // Load indicators panel
  _loadExplorerIndicators(sym);
}

async function _loadExplorerIndicators(sym) {
  const el  = document.getElementById('explorer-indicators');
  const res = await api(`/api/chart/indicators?symbol=${sym}&timeframes=${_explorerTf}`);
  if (!res.ok) return;
  const tf  = res.data[_explorerTf];
  if (!tf || !tf.indicators || !tf.indicators.ok) return;
  const ind = tf.indicators;

  const card = (label, value, sub, color = 'var(--accent2)') => `
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 16px">
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:6px">${label}</div>
      <div style="font-size:1.1rem;font-weight:700;color:${color}">${value}</div>
      ${sub ? `<div style="font-size:.75rem;color:var(--muted);margin-top:3px">${sub}</div>` : ''}
    </div>`;

  let html = '';

  if (ind.rsi) {
    const c = ind.rsi.value > 70 ? 'var(--red)' : ind.rsi.value < 30 ? 'var(--accent3)' : 'var(--accent2)';
    html += card('RSI (14)', ind.rsi.value, ind.rsi.signal, c);
  }
  if (ind.macd) {
    const c = ind.macd.trend === 'bullish' ? 'var(--accent3)' : 'var(--red)';
    const cross = ind.macd.crossover ? ' ← crossover' : ind.macd.crossunder ? ' ← crossunder' : '';
    html += card('MACD', ind.macd.trend.toUpperCase(), `Histogram ${ind.macd.histogram_trend}${cross}`, c);
  }
  if (ind.ema) {
    const bullish = ind.ema.alignment && ind.ema.alignment.startsWith('fully bullish');
    const bearish = ind.ema.alignment && ind.ema.alignment.startsWith('fully bearish');
    const c = bullish ? 'var(--accent3)' : bearish ? 'var(--red)' : 'var(--yellow)';
    html += card('EMAs', ind.ema.stack || 'Mixed', ind.ema.alignment, c);
  }
  if (ind.bollinger) {
    const c = ind.bollinger.position_pct > 80 ? 'var(--red)' : ind.bollinger.position_pct < 20 ? 'var(--accent3)' : 'var(--accent2)';
    html += card('Bollinger Bands', `${ind.bollinger.position_pct}th %ile`, ind.bollinger.signal, c);
  }
  if (ind.adx) {
    const c = ind.adx.value > 25 ? 'var(--yellow)' : 'var(--muted)';
    html += card('ADX (14)', ind.adx.value, `${ind.adx.strength}${ind.adx.direction ? ' · ' + ind.adx.direction : ''}`, c);
  }
  if (ind.stoch_rsi) {
    const c = ind.stoch_rsi.k > 80 ? 'var(--red)' : ind.stoch_rsi.k < 20 ? 'var(--accent3)' : 'var(--accent2)';
    html += card('Stoch RSI', `K ${ind.stoch_rsi.k} / D ${ind.stoch_rsi.d}`, ind.stoch_rsi.signal, c);
  }
  if (ind.atr) {
    html += card('ATR (14)', `${ind.atr.value}`, `${ind.atr.pct}% of price`, 'var(--muted)');
  }
  if (ind.volume) {
    const c = ind.volume.ratio > 1.5 ? 'var(--yellow)' : ind.volume.ratio < 0.7 ? 'var(--muted)' : 'var(--accent2)';
    html += card('Volume', `${ind.volume.ratio}× avg`, ind.volume.signal, c);
  }
  if (ind.support_resistance && ind.support_resistance.length) {
    const sr = ind.support_resistance;
    const sups = sr.filter(l => l.type === 'support').sort((a,b) => b.price - a.price);
    const ress = sr.filter(l => l.type === 'resistance').sort((a,b) => a.price - b.price);
    const lines = [];
    if (sups[0]) lines.push(`S: ${sups[0].price} (${sups[0].touches}×)`);
    if (ress[0]) lines.push(`R: ${ress[0].price} (${ress[0].touches}×)`);
    html += card('Key S/R', lines[0] || '—', lines[1] || '', 'var(--fg)');
  }

  el.innerHTML = html;
}

