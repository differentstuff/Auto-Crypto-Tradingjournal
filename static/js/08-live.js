
// ══════════════════════════════════════════════════════════════════════════════
// LIVE TRADES
// ══════════════════════════════════════════════════════════════════════════════
let liveTradesInterval  = null;
let livePositionsCache  = [];
let liveAnalysisCache   = {};   // key: "SYMBOL_direction" → analysis result dict
let liveOpenPanels      = new Set();  // indices of cards with open AI panels
let liveCallMatches     = {};   // key: "SYMBOL_direction" → saved call data
let liveMarketCtx       = {};   // key: symbol → {funding, long_short}

async function loadLiveTrades() {
  document.getElementById('trades-refresh-label').textContent = 'Refreshing…';
  try {
    // Fetch positions, matches, and waiting limits in parallel
    const [posRes, matchRes, limRes] = await Promise.all([
      api('/api/live/positions'),
      api('/api/calls/check-matches'),
      api('/api/limits?status=waiting'),
    ]);
    if (!posRes.ok) throw new Error(posRes.error);

    livePositionsCache = posRes.data.positions || [];
    const eq = posRes.data.equity || {};

    // Economic calendar warning (non-blocking)
    api('/api/market/calendar').then(cr => {
      const el = document.getElementById('eco-warning');
      if (!el) return;
      if (cr.ok && cr.data.length) {
        const lines = cr.data.map(e =>
          `📅 <strong>${e.title}</strong> ${e.when} ${e.time ? 'at ' + e.time + ' ET' : ''}` +
          `${e.forecast ? ' — Forecast: ' + e.forecast : ''}${e.previous ? ' · Prev: ' + e.previous : ''}`
        );
        el.innerHTML = '⚠ High-impact USD events:<br>' + lines.join('<br>');
        el.style.display = '';
      } else {
        el.style.display = 'none';
      }
    });

    // Fetch market context for all open symbols (non-blocking)
    if (livePositionsCache.length) {
      const syms = [...new Set(livePositionsCache.map(p => p.symbol))].join(',');
      api('/api/market/context?symbols=' + syms).then(mr => {
        if (mr.ok) {
          liveMarketCtx = mr.data.symbols || {};
          renderPositionCards(livePositionsCache, waitingLimits);  // re-render with context
        }
      });
    }

    // Build match map: key = "SYMBOL_direction" → call
    // Only store 'saved' status matches (need confirmation)
    const pendingMatches = {};
    const confirmedMatches = {};
    if (matchRes.ok) {
      (matchRes.data || []).forEach(m => {
        const key = m.call.symbol + '_' + m.call.direction;
        if (m.call.status === 'matched') confirmedMatches[key] = m.call;
        else                             pendingMatches[key]   = m.call;
      });
    }
    // Also fetch already-confirmed matches (status=matched)
    const savedRes = await api('/api/calls/saved');
    if (savedRes.ok) {
      savedRes.data.filter(c => c.status === 'matched').forEach(c => {
        confirmedMatches[c.symbol + '_' + c.direction] = c;
      });
    }

    liveCallMatches = { ...confirmedMatches };

    const waitingLimits = limRes.ok ? (limRes.data || []) : [];
    renderLiveKpis(livePositionsCache, eq);
    renderMatchBanners(pendingMatches, livePositionsCache);
    renderCorrelationWarning(livePositionsCache);
    renderPositionCards(livePositionsCache, waitingLimits);
    document.getElementById('trades-refresh-label').textContent =
      'Live · ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('trades-container').innerHTML =
      `<div class="upload-result error">❌ ${e.message}</div>`;
    document.getElementById('trades-refresh-label').textContent = 'Error';
  }
}

function renderMatchBanners(pendingMatches, positions) {
  const container = document.getElementById('match-confirmations');
  const entries   = Object.entries(pendingMatches);
  if (!entries.length) { container.innerHTML = ''; return; }

  container.innerHTML = entries.map(([key, call]) => {
    const pos = positions.find(p => p.symbol + '_' + p.direction === key);
    if (!pos) return '';
    const pnlStr = pos.unrealized_pnl >= 0 ? `+${fmtC(pos.unrealized_pnl)}` : fmtC(pos.unrealized_pnl);
    return `
    <div class="warn-box" style="margin-bottom:16px;display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap" id="match-banner-${call.id}">
      <div style="flex:1;min-width:200px">
        <strong style="font-size:.9rem;color:var(--text)">📡 Call Match Detected: ${call.symbol} ${call.direction}</strong>
        <div style="margin-top:4px;font-size:.8rem;line-height:1.5">
          You have an open ${pos.direction} on <strong>${pos.symbol}</strong> (${pnlStr} USDT unrealized)
          that matches your saved call from ${(call.created_at||'').slice(0,10)}.
          <br>Setup: ${call.setup_score||'?'}/10 ${call.setup_label||''} · ${call.trade_type||''} ·
          SL: <span style="color:var(--red)">${call.sl_price||'—'}</span> ·
          TP1: <span style="color:var(--accent3)">${call.tp1_price||'—'}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
        <button class="btn btn-primary btn-sm" onclick="confirmMatch(${call.id}, '${key}')">✅ Yes, this is that trade</button>
        <button class="btn btn-secondary btn-sm" onclick="dismissMatch(${call.id})">✗ Not this trade</button>
      </div>
    </div>`;
  }).join('');
}

async function confirmMatch(callId, key) {
  await api('/api/calls/' + callId + '/confirm-match', 'POST');
  document.getElementById('match-banner-' + callId)?.remove();
  // Fetch the call details and add to confirmed matches
  const savedRes = await api('/api/calls/saved');
  if (savedRes.ok) {
    const call = savedRes.data.find(c => c.id === callId);
    if (call) liveCallMatches[key] = call;
  }
  renderPositionCards(livePositionsCache);  // re-render with targets panel
}

async function dismissMatch(callId) {
  await api('/api/calls/' + callId + '/dismiss', 'POST');
  document.getElementById('match-banner-' + callId)?.remove();
}

function renderLiveKpis(positions, eq) {
  const totalUnrl  = positions.reduce((s, p) => s + p.unrealized_pnl, 0);
  const totalMargin= positions.reduce((s, p) => s + p.margin_usdt, 0);
  const equity     = parseFloat(eq.accountEquity || 0);
  const available  = parseFloat(eq.available || 0);
  const critical   = positions.filter(p => p.unrealized_pct < -30).length;

  const totalRisk = positions.reduce((s, p) => {
    const entry = parseFloat(p.entry_price || 0);
    const sl    = parseFloat(p.stop_loss  || 0);
    const size  = parseFloat(p.size_usdt  || 0);
    if (sl > 0 && entry > 0 && size > 0) {
      const slRisk = p.direction === 'Long'
        ? (entry - sl) / entry * size
        : (sl - entry) / entry * size;
      return s + Math.max(0, slRisk);
    }
    return s + (p.margin_usdt || 0);
  }, 0);
  const riskPct = equity > 0 ? (totalRisk / equity * 100).toFixed(1) : 0;
  const hasSl   = positions.some(p => parseFloat(p.stop_loss || 0) > 0);

  document.getElementById('trades-kpi-grid').innerHTML = [
    { label: 'Open Positions', value: positions.length, cls: 'neu', sub: `${critical} critical`,
      tip: `Number of currently open futures positions on Bitget. Critical = unrealized loss > 30% of margin (${critical} position${critical!==1?'s':''} flagged).` },
    { label: 'Total Unrealized P&L', value: (totalUnrl>=0?'+':'')+fmtC(totalUnrl)+' USDT',
      cls: pnlClass(totalUnrl), sub: 'Across all open trades',
      tip: 'Combined mark-to-market profit or loss across all open positions. Fluctuates with price — not realized until you close.' },
    { label: 'Margin In Use', value: fmtC(totalMargin)+' USDT', cls: 'neu', sub: 'Total collateral locked',
      tip: 'Total USDT collateral locked as margin across all open positions. Freed when positions close.' },
    { label: 'Account Equity', value: fmtC(equity)+' USDT', cls: 'neu', sub: available.toFixed(2)+' available',
      tip: 'Total account value including unrealized PnL. Available balance = equity minus margin currently in use.' },
    { label: 'Open Position Risk', value: fmtC(totalRisk)+' USDT', cls: totalRisk > 0 ? 'neg' : 'neu',
      sub: `${riskPct}% of equity${hasSl ? ' · SL-based' : ' · no SL'}`,
      tip: 'Maximum loss if all stop-losses trigger at once. Calculated as (entry − SL) / entry × size. Positions without SL use full margin as a conservative estimate.' },
  ].map(k => `<div class="kpi-card"${k.tip ? ` data-tip="${k.tip}"` : ''}>
    <div class="kpi-label">${k.label}</div>
    <div class="kpi-value ${k.cls}">${k.value}</div>
    <div class="kpi-sub">${k.sub||''}</div>
  </div>`).join('');
}

function renderCallTargetsPanel(call, pos) {
  const mark   = parseFloat(pos.mark_price || 0);
  const dir    = pos.direction === 'Long' ? 1 : -1;
  const tp1p   = parseFloat(call.tp1_price || 0);
  const beP    = parseFloat(pos.break_even_price || 0);
  const tp1Crossed = tp1p > 0 && mark > 0 && (
    (pos.direction === 'Long'  && mark >= tp1p) ||
    (pos.direction === 'Short' && mark <= tp1p)
  );

  function distRow(label, price, cls) {
    if (!price) return '';
    const p    = parseFloat(price);
    const dist = ((p - mark) / mark * 100 * dir);  // positive = moving toward target
    const distStr = (dist >= 0 ? '+' : '') + dist.toFixed(2) + '%';
    const distCol = dist >= 0 ? 'color:var(--accent3)' : 'color:var(--red)';
    return `
      <div class="target-cell ${cls}">
        <div class="target-cell-label">${label}</div>
        <div class="target-cell-price" style="${cls.includes('sl') ? 'color:var(--red)' : 'color:var(--accent3)'}">${p}</div>
        <div class="target-cell-dist" style="${distCol}">${distStr} from mark</div>
      </div>`;
  }

  const entryDist = call.avg_entry
    ? (((parseFloat(call.avg_entry) - mark) / mark * 100 * dir * -1)).toFixed(2)
    : null;

  return `
    <div class="call-targets-panel">
      <h4>📡 Linked Call — ${call.trade_type || ''} · ${call.setup_score || '?'}/10 ${call.setup_label || ''} · R:R ${call.rr_ratio || '—'}</h4>
      <div class="targets-grid">
        ${call.tp1_price ? distRow('Take Profit 1', call.tp1_price, 'target-tp') : ''}
        ${call.tp2_price ? distRow('Take Profit 2', call.tp2_price, 'target-tp') : ''}
        ${call.sl_price  ? distRow('Stop Loss',      call.sl_price,  'target-sl') : ''}
        ${call.avg_entry ? `
        <div class="target-cell">
          <div class="target-cell-label">Call Avg Entry</div>
          <div class="target-cell-price" style="color:var(--accent2)">${parseFloat(call.avg_entry).toPrecision(5)}</div>
          <div class="target-cell-dist" style="${parseFloat(entryDist) >= 0 ? 'color:var(--accent3)' : 'color:var(--red)'}">
            ${parseFloat(entryDist) >= 0 ? '+' : ''}${entryDist}% from mark
          </div>
        </div>` : ''}
      </div>
      ${tp1Crossed ? `
        <div class="be-prompt">
          ✅ <strong>TP1 reached</strong> — consider moving Stop Loss to break-even (${beP > 0 ? beP.toPrecision(5) : 'entry price'}) to protect profits
        </div>` : ''}
      ${call.has_candle_close_sl ? `
        <div class="candle-sl-chip">⚠ Candle-close SL at ${call.sl_price} — monitor manually, close on 4H close below</div>` : ''}
      ${call.entry_timing ? `
        <div style="font-size:.75rem;color:var(--muted);margin-top:8px"><strong style="color:var(--text)">Entry timing:</strong> ${call.entry_timing}</div>` : ''}
      <div style="margin-top:10px;display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="closeCall(${call.id});loadLiveTrades()">Mark Call Closed</button>
      </div>
    </div>`;
}

function renderPositionCards(positions, waitingLimits) {
  const container = document.getElementById('trades-container');
  if (!positions.length) {
    container.innerHTML = `<div class="no-positions">
      <div class="icon">😴</div>
      <div style="font-weight:600;margin-bottom:6px">No open positions</div>
      <div>All positions are closed. Good time to review your journal!</div>
    </div>`;
    return;
  }

  // Re-index open panels by symbol_dir key so they survive position reorder
  const openByKey = new Set(
    [...liveOpenPanels].map(i => {
      const old = livePositionsCache[i];
      return old ? old.symbol + '_' + old.direction : null;
    }).filter(Boolean)
  );
  liveOpenPanels.clear();

  container.innerHTML = positions.map((p, i) => {
    const key        = p.symbol + '_' + p.direction;
    const isCritical = p.unrealized_pct < -30;
    const isLoss     = p.unrealized_pnl < 0;
    const cardClass  = isCritical ? 'critical' : (isLoss ? 'loss' : 'profit');
    const pnlCol     = isLoss ? 'neg' : 'pos';
    const noSl       = !p.stop_loss;
    const noTp       = !p.take_profit;
    const dur        = p.duration_minutes != null ? durFmt(p.duration_minutes) : '—';
    const hadAnalysis = liveAnalysisCache[key];
    const relLimits  = (waitingLimits || []).filter(l => l.symbol === p.symbol);
    const is48h      = p.duration_minutes != null && p.duration_minutes > 2880;
    const liqDist    = (() => {
      const mark = parseFloat(p.mark_price || 0);
      const liq  = parseFloat(p.liquidation_price || 0);
      if (!liq || !mark) return Infinity;
      return Math.abs((liq - mark) / mark * 100);
    })();
    const isLiqNear  = liqDist < 15;

    return `
    <div class="position-card ${cardClass}" id="card-${i}">
      <div class="pos-header" onclick="togglePositionDetail(${i})">
        <!-- Symbol + badges -->
        <div>
          <div class="pos-symbol">${p.symbol}</div>
          <div class="pos-badge" style="margin-top:4px">
            <span class="badge ${p.direction.toLowerCase()}">${p.direction}</span>
            <span class="badge" style="background:rgba(108,99,255,.15);color:var(--accent)">${p.leverage}x</span>
            ${noSl ? '<span class="badge" style="background:rgba(239,83,80,.15);color:var(--red);font-size:.65rem">NO SL</span>' : ''}
            ${isCritical ? '<span class="badge" style="background:rgba(239,83,80,.25);color:var(--red);animation:pulse 1.5s infinite">⚠ CRITICAL</span>' : ''}
            ${is48h ? `<span class="chip-48h">⏱ ${Math.floor(p.duration_minutes/1440)}d+ OPEN</span>` : ''}
            ${isLiqNear ? `<span class="chip-liq-warn">⚡ LIQ ${liqDist.toFixed(1)}% AWAY</span>` : ''}
            ${relLimits.length ? `<span class="badge" style="background:rgba(79,195,247,.12);color:var(--accent2);font-size:.65rem;cursor:pointer" onclick="event.stopPropagation();showPage('pending')" title="${relLimits.map(l=>`${l.direction} @ ${l.limit_price}`).join(', ')}">⏳ ${relLimits.length} limit${relLimits.length>1?'s':''}</span>` : ''}
            ${(() => {
              const mc = liveMarketCtx[p.symbol] || {};
              const chips = [];
              const fr = mc.funding || {};
              if (fr.ok) {
                const col = fr.rate > 0 ? 'var(--yellow)' : 'var(--accent3)';
                const bg  = fr.rate > 0 ? 'rgba(255,179,0,.12)' : 'rgba(38,217,107,.12)';
                chips.push(`<span class="badge" style="background:${bg};color:${col};font-size:.65rem">F ${fr.rate_pct > 0 ? '+' : ''}${fr.rate_pct}%${fr.high ? ' ⚠' : ''}</span>`);
              }
              const ls = mc.long_short || {};
              if (ls.ok) {
                const crowded = ls.long_pct > 65 || ls.short_pct > 65;
                const col = crowded ? 'var(--yellow)' : 'var(--muted)';
                chips.push(`<span class="badge" style="background:rgba(121,134,203,.1);color:${col};font-size:.65rem">L/S ${ls.long_pct}/${ls.short_pct}</span>`);
              }
              return chips.join('');
            })()}
          </div>
        </div>
        <!-- Stats -->
        <div class="pos-stat">
          <div class="pos-stat-label">Size</div>
          <div class="pos-stat-val">${fmtC(p.size_usdt)} USDT</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Entry</div>
          <div class="pos-stat-val">${parseFloat(p.entry_price).toPrecision(5)}</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Mark Price</div>
          <div class="pos-stat-val">${parseFloat(p.mark_price).toPrecision(5)}</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Unrealized P&L</div>
          <div class="pos-stat-val ${pnlCol}">${p.unrealized_pnl>=0?'+':''}${fmtC(p.unrealized_pnl)} USDT</div>
          <div style="font-size:.7rem;${isLoss?'color:var(--red)':'color:var(--accent3)'}">${p.unrealized_pct>=0?'+':''}${p.unrealized_pct}%</div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">TP / SL</div>
          <div class="pos-stat-val" style="font-size:.8rem">
            <span style="color:var(--accent3)">${p.take_profit || '—'}</span> /
            <span style="color:var(--red)">${p.stop_loss || '—'}</span>
          </div>
        </div>
        <div class="pos-stat">
          <div class="pos-stat-label">Open</div>
          <div class="pos-stat-val" style="font-size:.8rem">${dur}</div>
        </div>
        <!-- Actions -->
        <div class="pos-actions">
          <button class="btn-chart-sm" title="S/R Chart"
                  onclick="event.stopPropagation();openChart('${p.symbol}')">📊 Chart</button>
          <button class="btn-ai-trade" id="ai-btn-${i}"
                  onclick="event.stopPropagation();analyzePosition(${i})">
            ${hadAnalysis ? '🔄 Re-analyze' : '🤖 AI Analysis'}
          </button>
        </div>
      </div>
      <!-- Expandable detail row -->
      <div style="padding:0 20px;display:none;font-size:.78rem;color:var(--muted);border-top:1px solid var(--border)" id="detail-${i}">
        <div style="display:flex;gap:24px;padding:10px 0;flex-wrap:wrap">
          <span>Break even: <strong>${parseFloat(p.break_even_price||0).toPrecision(5)}</strong></span>
          <span>Liquidation: <strong style="color:var(--red)">${parseFloat(p.liquidation_price||0).toPrecision(5)}</strong></span>
          <span>Margin: <strong>${fmtC(p.margin_usdt)} USDT (${p.margin_mode})</strong></span>
          <span>Fees paid: <strong>${fmtC(p.total_fee)}</strong></span>
          <span>Realized so far: <strong class="${pnlClass(p.achieved_profits)}">${p.achieved_profits>=0?'+':''}${fmtC(p.achieved_profits)}</strong></span>
        </div>
      </div>
      <!-- Call targets panel (shown when matched to a saved call) -->
      ${liveCallMatches[key] ? renderCallTargetsPanel(liveCallMatches[key], p) : ''}
      <!-- AI Analysis Panel — pre-open if it was open before the refresh -->
      <div class="pos-ai-panel${openByKey.has(key) ? ' open' : ''}" id="ai-panel-${i}"></div>
    </div>`;
  }).join('');

  // Restore cached AI analyses into the freshly rendered cards
  positions.forEach((p, i) => {
    const key = p.symbol + '_' + p.direction;
    if (liveAnalysisCache[key]) {
      renderTradeAnalysis(i, liveAnalysisCache[key]);
      liveOpenPanels.add(i);
    }
  });
}

function togglePositionDetail(i) {
  const d = document.getElementById(`detail-${i}`);
  d.style.display = d.style.display === 'none' ? 'flex' : 'none';
}

async function analyzePosition(idx) {
  const position = livePositionsCache[idx];
  if (!position) return;

  const btn   = document.getElementById(`ai-btn-${idx}`);
  const panel = document.getElementById(`ai-panel-${idx}`);

  btn.disabled  = true;
  btn.textContent = '⏳ Analyzing…';
  panel.className = 'pos-ai-panel open';
  panel.innerHTML = `<div class="ai-trade-loading">
    <div class="spinner"></div>
    <div>Claude is analyzing your ${position.symbol} ${position.direction} position…</div>
  </div>`;

  try {
    const res = await api('/api/live/analyze', 'POST', position);
    if (!res.ok) throw new Error(res.error);
    // Save to cache so the panel survives auto-refresh
    const key = position.symbol + '_' + position.direction;
    liveAnalysisCache[key] = res.data;
    liveOpenPanels.add(idx);
    renderTradeAnalysis(idx, res.data);
    btn.textContent = '🔄 Re-analyze';
    btn.disabled = false;
  } catch(e) {
    panel.innerHTML = `<div class="upload-result error" style="margin:16px">❌ ${e.message}</div>`;
    btn.textContent = '🤖 AI Analysis';
    btn.disabled = false;
  }
}

function renderTradeAnalysis(idx, d) {
  const panel = document.getElementById(`ai-panel-${idx}`);
  const risk  = d.risk_rating || {};
  const riskLabel = (risk.label || 'Unknown').toLowerCase().replace(' ', '');
  const actionClass = {
    'Hold': 'hold',
    'Close Now': 'close-now',
    'Partial Close': 'partial',
    'Adjust SL': 'partial',
  }[d.action] || 'hold';
  const urgClass = {
    'Immediate': 'urgency-immediate',
    'Today': 'urgency-today',
    'No rush': 'urgency-norush',
  }[d.time_urgency] || 'urgency-norush';

  const hist  = d._history || {};
  const tp    = d.tp_recommendation || {};
  const sl    = d.sl_recommendation || {};

  panel.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px">
      <span class="risk-badge risk-${riskLabel}">Risk ${risk.value}/10 · ${risk.label}</span>
      <span class="urgency-chip ${urgClass}">${d.time_urgency}</span>
      ${hist.trades ? `<span style="font-size:.75rem;color:var(--muted)">Based on ${hist.trades} past ${d._symbol} trades (${hist.win_rate_pct}% WR, ${hist.total_pnl >= 0 ? '+' : ''}${fmtC(hist.total_pnl)} USDT total)</span>` : ''}
    </div>

    <div class="action-box ${actionClass}">
      <div class="action-title">Recommended Action</div>
      <div class="action-val">${d.action}</div>
      <div style="font-size:.82rem;color:var(--muted);margin-top:4px">${d.action_reason || ''}</div>
    </div>

    <div class="ai-overall" style="margin:12px 0">${d.summary || ''}</div>

    ${(tp.price || sl.price) ? `
    <div class="tp-sl-grid">
      ${tp.price ? `<div class="tp-sl-card">
        <h4>🟢 Take Profit</h4>
        <div class="tp-sl-price tp">${tp.price}</div>
        <div class="tp-sl-rationale">${tp.rationale || ''}</div>
      </div>` : '<div></div>'}
      ${sl.price ? `<div class="tp-sl-card">
        <h4>🔴 Stop Loss</h4>
        <div class="tp-sl-price sl">${sl.price}</div>
        <div class="tp-sl-rationale">${sl.rationale || ''}</div>
      </div>` : '<div></div>'}
    </div>` : ''}

    ${d.key_risks?.length ? `
    <div style="margin-top:12px">
      <div style="font-size:.75rem;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:6px">Key Risks</div>
      <ul class="risk-list">${d.key_risks.map(r => `<li>${r}</li>`).join('')}</ul>
    </div>` : ''}

    ${d.historical_context ? `
    <div style="font-size:.78rem;color:var(--muted);margin-top:10px;padding:8px 12px;background:var(--bg3);border-radius:6px">
      📊 ${d.historical_context}
    </div>` : ''}

    <div style="font-size:.7rem;color:var(--border);margin-top:12px;text-align:right">
      ${d._input_tokens} in / ${d._output_tokens} out tokens · claude-sonnet-4-6
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// CORRELATION WARNING
// ══════════════════════════════════════════════════════════════════════════════
function renderCorrelationWarning(positions) {
  const el = document.getElementById('correlation-warning');
  if (!el) return;

  const SECTORS = {
    'Bitcoin':    ['BTCUSDT','WBTCUSDT'],
    'ETH / L2':   ['ETHUSDT','ARBUSDT','OPUSDT','MATICUSDT','STRKUSDT','ZKUSDT','SCROLLUSDT'],
    'SOL / L1':   ['SOLUSDT','AVAXUSDT','SUIUSDT','APTUSDT','NEARUSDT','SEIUSDT','INJUSDT'],
    'Meme':       ['DOGEUSDT','SHIBUSDT','PEPEUSDT','BOMEUSDT','WIFUSDT','BONKUSDT','FLOKIUSDT','MOGUSDT','POPCATUSDT'],
    'DeFi':       ['UNIUSDT','AAVEUSDT','CRVUSDT','MKRUSDT','SNXUSDT','COMPUSDT','DYDXUSDT'],
    'AI / Infra': ['FETUSDT','RENDERUSDT','WLDUSDT','TAOUSDT','AGIXUSDT','GRTUSDT'],
  };

  const warnings = [];

  // Sector correlation: 2+ positions in same sector, same direction
  for (const [sector, symbols] of Object.entries(SECTORS)) {
    for (const dir of ['Long', 'Short']) {
      const group = positions.filter(p => p.direction === dir && symbols.includes(p.symbol));
      if (group.length >= 2) {
        const exposure = group.reduce((s, p) => s + (p.size_usdt || p.margin_usdt || 0), 0);
        const severity = group.length >= 3 ? '🔴' : '🟡';
        warnings.push({
          severity: group.length >= 3 ? 2 : 1,
          text: `${severity} ${group.length}× ${dir} in <strong>${sector}</strong> sector `
              + `(${group.map(p=>p.symbol).join(', ')}) — `
              + `${fmtC(exposure)} USDT exposure moves together`,
        });
      }
    }
  }

  // Directional overload: 3+ positions all same direction across any sectors
  const longs  = positions.filter(p => p.direction === 'Long');
  const shorts  = positions.filter(p => p.direction === 'Short');
  if (longs.length >= 3) {
    const m = longs.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 2, text: `🔴 ${longs.length} simultaneous LONG positions — one BTC dump hits all of them (${fmtC(m)} USDT margin)` });
  } else if (longs.length === 2) {
    const m = longs.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 1, text: `🟡 2 simultaneous LONG positions (${fmtC(m)} USDT margin) — correlated downside risk` });
  }
  if (shorts.length >= 3) {
    const m = shorts.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 2, text: `🔴 ${shorts.length} simultaneous SHORT positions (${fmtC(m)} USDT margin) — correlated upside risk` });
  } else if (shorts.length === 2) {
    const m = shorts.reduce((s,p) => s + (p.margin_usdt || 0), 0);
    warnings.push({ severity: 1, text: `🟡 2 simultaneous SHORT positions (${fmtC(m)} USDT margin) — correlated upside risk` });
  }

  // Deduplicate and sort by severity
  const unique = [...new Map(warnings.map(w => [w.text, w])).values()]
    .sort((a, b) => b.severity - a.severity);

  if (unique.length) {
    const bg = unique.some(w => w.severity >= 2) ? 'rgba(239,83,80,.12)' : 'rgba(255,179,0,.10)';
    el.style.background = bg;
    el.innerHTML = unique.map(w => `<div style="margin-bottom:4px">${w.text}</div>`).join('');
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}
