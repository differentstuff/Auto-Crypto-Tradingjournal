
// ══════════════════════════════════════════════════════════════════════════════
// BITGET LIVE PENDING ORDERS
// ══════════════════════════════════════════════════════════════════════════════
let _bitgetOrdersCache = null;   // { entry:[], exit:[], tracked_ids:[], fetched_at }

async function loadBitgetOrders() {
  const content = document.getElementById('bitget-orders-content');
  const ageEl   = document.getElementById('bitget-orders-age');
  const btn     = document.getElementById('bitget-refresh-btn');
  if (!content) return;

  content.innerHTML = '<span style="color:var(--muted);font-size:.82rem">Fetching from Bitget…</span>';
  if (btn) btn.disabled = true;

  const res = await api('/api/live/pending-orders');
  if (btn) btn.disabled = false;

  if (!res.ok) {
    content.innerHTML = `<span style="color:var(--red);font-size:.82rem">❌ ${res.error}</span>`;
    return;
  }

  _bitgetOrdersCache = {
    entry:       res.data.bitget_orders?.entry  || [],
    exit:        res.data.bitget_orders?.exit   || [],
    tracked_ids: new Set(res.data.tracked_ids   || []),
    fetched_at:  new Date(),
  };

  renderBitgetOrdersSection();
  if (ageEl) ageEl.textContent = 'Updated just now';
}

function renderBitgetOrdersSection() {
  const content = document.getElementById('bitget-orders-content');
  if (!content || !_bitgetOrdersCache) return;

  const { entry, exit, tracked_ids } = _bitgetOrdersCache;

  if (!entry.length && !exit.length) {
    content.innerHTML = '<span style="color:var(--muted);font-size:.82rem">No pending limit orders on Bitget right now.</span>';
    return;
  }

  let html = '';

  if (entry.length) {
    html += `<div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:8px">Entry Orders (${entry.length})</div>`;
    html += entry.map(o => renderBitgetOrderRow(o, tracked_ids, false)).join('');
  }

  if (exit.length) {
    html += `<div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;font-weight:700;margin:14px 0 8px">Exit / TP-SL Orders (${exit.length})</div>`;
    html += exit.map(o => renderBitgetOrderRow(o, tracked_ids, true)).join('');
  }

  content.innerHTML = html;
}

function renderBitgetOrderRow(o, trackedIds, isExit) {
  const tracked  = trackedIds.has(o.order_id);
  const dirClass = o.direction === 'Long' ? 'long' : 'short';
  const rowClass = isExit ? 'bitget-order-row exit-order-row' : 'bitget-order-row';

  const trackBtn = (!isExit && !tracked)
    ? `<button class="btn btn-sm" style="background:rgba(108,99,255,.15);color:var(--accent);border:1px solid rgba(108,99,255,.3);white-space:nowrap" onclick='openMatchModal(${JSON.stringify(o)})'>🔗 Track &amp; Match</button>`
    : (tracked ? `<span class="tracked-badge">✓ Tracked</span>` : '');

  const notional = o.notional_usdt ? `<span style="color:var(--yellow)">${fmtC(o.notional_usdt)} USDT</span>` : '';
  const lev      = o.leverage ? `<span style="font-size:.75rem;color:var(--muted)">${o.leverage}x</span>` : '';

  return `<div class="${rowClass}">
    <span style="font-weight:700;min-width:90px">${escHtml(o.symbol)}</span>
    <span class="badge ${dirClass}">${o.direction}</span>
    ${isExit ? `<span style="font-size:.72rem;background:rgba(239,83,80,.12);color:var(--red);padding:2px 8px;border-radius:20px">Exit</span>` : ''}
    <span style="color:var(--accent2);font-weight:600">@ ${o.price}</span>
    ${notional} ${lev}
    <span style="font-size:.75rem;color:var(--muted);margin-left:4px">${o.created_at}</span>
    <span style="margin-left:auto">${trackBtn}</span>
  </div>`;
}

let _matchOrderData = null;

async function openMatchModal(order) {
  _matchOrderData = order;
  document.getElementById('match-order-id').value     = order.order_id;
  document.getElementById('match-sl-price').value     = '';
  document.getElementById('match-tp1-price').value    = '';
  document.getElementById('match-tp2-price').value    = '';
  document.getElementById('match-notes').value        = '';
  document.getElementById('match-selected-call-id').value = '';

  // Build order summary card
  document.getElementById('match-order-summary').innerHTML = `
    <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
      <span style="font-size:1.1rem;font-weight:700">${escHtml(order.symbol)}</span>
      <span class="badge ${order.direction==='Long'?'long':'short'}">${order.direction}</span>
      <div><div style="font-size:.7rem;color:var(--muted)">Limit Price</div><div style="font-weight:700;color:var(--accent2)">${order.price}</div></div>
      ${order.notional_usdt ? `<div><div style="font-size:.7rem;color:var(--muted)">Notional</div><div style="font-weight:700;color:var(--yellow)">${fmtC(order.notional_usdt)} USDT</div></div>` : ''}
      ${order.leverage ? `<div><div style="font-size:.7rem;color:var(--muted)">Leverage</div><div style="font-weight:700">${order.leverage}x</div></div>` : ''}
      <div style="margin-left:auto;font-size:.75rem;color:var(--muted)">${order.created_at}</div>
    </div>`;

  // Load saved calls for picker
  const picker = document.getElementById('match-call-picker');
  picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">Loading…</div>';

  const res = await api('/api/calls/saved');
  if (!res.ok || !res.data.length) {
    picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">No saved calls found. Analyze and save a call first, or track without linking.</div>';
  } else {
    // Sort: same symbol+direction first
    const sym = order.symbol;
    const dir = order.direction;
    const sorted = [...res.data].sort((a, b) => {
      const aMatch = (a.symbol === sym && a.direction === dir) ? 0 : 1;
      const bMatch = (b.symbol === sym && b.direction === dir) ? 0 : 1;
      return aMatch - bMatch;
    });

    picker.innerHTML = sorted.map(c => {
      const isMatch = c.symbol === sym && c.direction === dir;
      return `<div class="outcome-option" id="call-pick-${c.id}" onclick="selectCallForMatch(${c.id})" style="${isMatch ? 'border-color:var(--accent);' : ''}">
        <input type="radio" name="match-call-radio" value="${c.id}" style="width:14px;height:14px;accent-color:var(--accent)">
        <div style="flex:1">
          <div style="font-weight:600;font-size:.85rem">
            ${escHtml(c.symbol)} ${c.direction}
            ${isMatch ? '<span style="color:var(--accent3);font-size:.72rem"> ✓ matches this order</span>' : ''}
          </div>
          <div style="font-size:.75rem;color:var(--muted)">
            Score: ${c.setup_score||'—'}/10 · ${c.trade_type||''} ·
            SL: ${c.sl_price||'—'} · TP1: ${c.tp1_price||'—'}
            ${c.analyst ? ' · 📡 '+escHtml(c.analyst) : ''}
            · ${(c.created_at||'').slice(0,10)}
          </div>
        </div>
      </div>`;
    }).join('');
  }

  document.getElementById('match-modal').classList.add('open');
}

function selectCallForMatch(callId) {
  document.getElementById('match-selected-call-id').value = callId;
  // Tick the radio + highlight
  document.querySelectorAll('input[name="match-call-radio"]').forEach(r => {
    r.checked = (parseInt(r.value) === callId);
  });
  document.querySelectorAll('#match-call-picker .outcome-option').forEach(el => {
    el.style.background = '';
  });
  const picked = document.getElementById('call-pick-' + callId);
  if (picked) picked.style.background = 'rgba(108,99,255,.08)';
}

function closeMatchModal() {
  document.getElementById('match-modal').classList.remove('open');
  _matchOrderData = null;
}

async function saveMatchedOrder() {
  if (!_matchOrderData) return;
  const o       = _matchOrderData;
  const callId  = document.getElementById('match-selected-call-id').value || null;

  // Look up analyst from the selected call
  let analyst = '';
  if (callId) {
    const radio = document.querySelector(`input[name="match-call-radio"][value="${callId}"]`);
    const callRow = radio?.closest('.outcome-option');
    if (callRow) {
      const analystSpan = callRow.querySelector('.analyst-tag');
      if (analystSpan) analyst = analystSpan.textContent.replace('📡','').trim();
    }
    // Try from the summary text
    const matchText = callRow?.textContent || '';
    const m = matchText.match(/📡\s*([^\s·]+)/);
    if (m) analyst = m[1].trim();
  }

  const body = {
    symbol:          o.symbol,
    direction:       o.direction,
    limit_price:     o.price,
    size_usdt:       o.notional_usdt || null,
    leverage:        parseInt(o.leverage) || 10,
    sl_price:        parseFloat(document.getElementById('match-sl-price').value)  || null,
    tp1_price:       parseFloat(document.getElementById('match-tp1-price').value) || null,
    tp2_price:       parseFloat(document.getElementById('match-tp2-price').value) || null,
    notes:           document.getElementById('match-notes').value.trim(),
    call_id:         callId ? parseInt(callId) : null,
    analyst,
    bitget_order_id: o.order_id,
  };

  const res = await api('/api/limits', 'POST', body);
  if (res.ok) {
    closeMatchModal();
    loadBitgetOrders();
    loadPendingLimits('waiting');
  } else {
    alert('Error: ' + res.error);
  }
}

document.getElementById('match-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeMatchModal();
});

async function openLinkCallModal(limitId) {
  // Re-use match modal for linking an existing pending_limit to a call
  _matchOrderData = { _existing_limit_id: limitId };

  // Fetch the limit to get its symbol/direction
  const res = await api('/api/limits?status=waiting');
  const lim  = (res.data || []).find(l => l.id === limitId);
  if (!lim) return;

  document.getElementById('match-order-id').value = '';
  document.getElementById('match-sl-price').value  = lim.sl_price  || '';
  document.getElementById('match-tp1-price').value = lim.tp1_price || '';
  document.getElementById('match-tp2-price').value = lim.tp2_price || '';
  document.getElementById('match-notes').value     = lim.notes     || '';
  document.getElementById('match-selected-call-id').value = '';

  document.getElementById('match-order-summary').innerHTML = `
    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">
      <span style="font-size:1rem;font-weight:700">${escHtml(lim.symbol)}</span>
      <span class="badge ${lim.direction==='Long'?'long':'short'}">${lim.direction}</span>
      <div><div style="font-size:.7rem;color:var(--muted)">Limit Price</div><div style="font-weight:700;color:var(--accent2)">${lim.limit_price}</div></div>
      ${lim.size_usdt ? `<div><div style="font-size:.7rem;color:var(--muted)">Size</div><div style="font-weight:700;color:var(--yellow)">${fmtC(lim.size_usdt)} USDT</div></div>` : ''}
      <span style="font-size:.75rem;color:var(--muted);margin-left:auto">Limit #${limitId}</span>
    </div>`;

  // Load call picker (same as openMatchModal)
  const picker = document.getElementById('match-call-picker');
  picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">Loading…</div>';

  const callsRes = await api('/api/calls/saved');
  if (!callsRes.ok || !callsRes.data.length) {
    picker.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.82rem">No saved calls found.</div>';
  } else {
    const sym = lim.symbol; const dir = lim.direction;
    const sorted = [...callsRes.data].sort((a, b) =>
      ((a.symbol===sym&&a.direction===dir)?0:1) - ((b.symbol===sym&&b.direction===dir)?0:1)
    );
    picker.innerHTML = sorted.map(c => {
      const isMatch = c.symbol === sym && c.direction === dir;
      return `<div class="outcome-option" id="call-pick-${c.id}" onclick="selectCallForMatch(${c.id})" style="${isMatch?'border-color:var(--accent);':''}">
        <input type="radio" name="match-call-radio" value="${c.id}" style="width:14px;height:14px;accent-color:var(--accent)">
        <div style="flex:1">
          <div style="font-weight:600;font-size:.85rem">${escHtml(c.symbol)} ${c.direction}
            ${isMatch?'<span style="color:var(--accent3);font-size:.72rem"> ✓ matches</span>':''}
          </div>
          <div style="font-size:.75rem;color:var(--muted)">Score: ${c.setup_score||'—'}/10 · ${c.trade_type||''} · SL: ${c.sl_price||'—'} · TP1: ${c.tp1_price||'—'}${c.analyst?' · 📡 '+escHtml(c.analyst):''} · ${(c.created_at||'').slice(0,10)}</div>
        </div>
      </div>`;
    }).join('');
  }

  document.getElementById('match-modal').classList.add('open');
}

// Override saveMatchedOrder to handle existing limit case
const _origSaveMatchedOrder = saveMatchedOrder;
saveMatchedOrder = async function() {
  if (_matchOrderData?._existing_limit_id) {
    // Linking an existing limit to a call
    const limitId = _matchOrderData._existing_limit_id;
    const callId  = document.getElementById('match-selected-call-id').value;
    const updates = {
      call_id:   callId ? parseInt(callId) : null,
      sl_price:  parseFloat(document.getElementById('match-sl-price').value)  || null,
      tp1_price: parseFloat(document.getElementById('match-tp1-price').value) || null,
      tp2_price: parseFloat(document.getElementById('match-tp2-price').value) || null,
      notes:     document.getElementById('match-notes').value.trim(),
    };
    const res = await api('/api/limits/' + limitId, 'PATCH', updates);
    if (res.ok) { closeMatchModal(); loadPendingLimits('waiting'); }
    else alert('Error: ' + res.error);
    return;
  }
  await _origSaveMatchedOrder();
};

// ══════════════════════════════════════════════════════════════════════════════
// PENDING LIMIT ORDERS
// ══════════════════════════════════════════════════════════════════════════════
let currentLimitStatus = 'waiting';

async function loadPendingLimits(status) {
  currentLimitStatus = status || 'waiting';
  // Update tab button highlights
  ['waiting','triggered','cancelled'].forEach(s => {
    const btn = document.getElementById('pending-tab-' + s);
    if (!btn) return;
    btn.style.borderColor = s === currentLimitStatus ? 'var(--accent)' : '';
    btn.style.color       = s === currentLimitStatus ? 'var(--accent)' : '';
  });

  const list = document.getElementById('pending-limits-list');
  if (!list) return;
  list.innerHTML = '<div class="ai-loading"><div class="spinner"></div><p>Loading…</p></div>';

  if (currentLimitStatus === 'waiting') loadPendingRiskSummary();
  else { const b = document.getElementById('pending-risk-banner'); if (b) b.style.display = 'none'; }

  const res = await api('/api/limits?status=' + currentLimitStatus);
  if (!res.ok) { list.innerHTML = '<p style="color:var(--red);padding:20px">Error loading orders</p>'; return; }

  if (!res.data.length) {
    const msgs = {
      waiting:   'No pending limit orders. Click "+ Add Limit Order" to track a trade setup.',
      triggered: 'No triggered limits yet.',
      cancelled: 'No cancelled limits.',
    };
    list.innerHTML = `<div class="no-positions"><div class="icon">⏳</div><p style="color:var(--muted)">${msgs[currentLimitStatus]}</p></div>`;
    return;
  }
  list.innerHTML = res.data.map(renderPendingLimitCard).join('');
  renderLimitCards(res.data);  // mobile cards

  // Proximity check for waiting limits — fetch current prices and annotate cards
  if (currentLimitStatus === 'waiting' && res.data.length) {
    const symbols = [...new Set(res.data.map(l => l.symbol).filter(Boolean))];
    api('/api/market/prices?symbols=' + symbols.join(',')).then(pr => {
      if (!pr.ok) return;
      res.data.forEach(lim => {
        const mark = pr.data[lim.symbol];
        if (!mark || !lim.limit_price) return;
        const dist = Math.abs((lim.limit_price - mark) / mark * 100);
        if (dist > 5) return;
        const el = document.getElementById('prox-' + lim.id);
        if (!el) return;
        const col = dist < 1 ? 'var(--red)' : dist < 3 ? 'var(--yellow)' : 'var(--accent2)';
        el.innerHTML = `<span style="font-size:.72rem;padding:2px 8px;border-radius:4px;background:rgba(255,255,255,.06);color:${col}">📍 ${dist.toFixed(1)}% from limit</span>`;
        el.style.display = '';
      });
    });
  }
}

async function loadPendingRiskSummary() {
  const banner = document.getElementById('pending-risk-banner');
  if (!banner) return;
  const res = await api('/api/limits/risk-summary');
  if (!res.ok || !res.data.pending_count) { banner.style.display = 'none'; return; }
  const d = res.data;
  banner.style.display = '';
  const syms = d.by_symbol.map(s => `${s.symbol}: <strong>${fmtC(s.notional)} USDT</strong>`).join(' · ');
  banner.innerHTML = `<strong>⚠ ${d.pending_count} pending order${d.pending_count!==1?'s':''} · ${fmtC(d.total_notional_usdt)} USDT notional committed if all fill</strong>${syms ? `<div style="margin-top:4px;font-size:.75rem;opacity:.85">${syms}</div>` : ''}`;
}

function renderPendingLimitCard(lim) {
  const chipClass = 'limit-chip-' + lim.status;
  const chipLabel = {waiting:'⏳ Waiting', triggered:'✅ Triggered', cancelled:'✕ Cancelled'}[lim.status] || lim.status;
  const dirClass  = lim.direction === 'Long' ? 'long' : 'short';

  // Stop distance & risk
  let riskStr = '';
  if (lim.limit_price && lim.sl_price && lim.size_usdt) {
    const stopDist = Math.abs(lim.limit_price - lim.sl_price) / lim.limit_price * 100;
    const riskUsdt = lim.size_usdt * stopDist / 100;
    riskStr = `Risk if SL: <span class="neg">${fmtC(riskUsdt)} USDT</span> (${stopDist.toFixed(1)}% stop)`;
  }

  // R:R to TP1
  let rrStr = '';
  if (lim.tp1_price && lim.sl_price && lim.limit_price) {
    const gain = Math.abs(lim.tp1_price - lim.limit_price);
    const loss = Math.abs(lim.limit_price - lim.sl_price);
    if (loss > 0) rrStr = ` · R:R <strong style="color:var(--accent)">${(gain/loss).toFixed(1)}:1</strong>`;
  }

  // Stored AI verdict
  let verdictHtml = '';
  if (lim.analysis_json) {
    try {
      const a = JSON.parse(lim.analysis_json);
      // ai_limit.py returns: recommendation, setup_quality.score, risk_assessment, entry_quality
      const rec = a.recommendation || a.verdict;
      if (rec) {
        const isKeep   = rec === 'Keep';
        const isCancel = rec === 'Cancel';
        const vColor   = isKeep ? 'var(--accent3)' : isCancel ? 'var(--red)' : 'var(--yellow)';
        const score    = a.setup_quality?.score ?? a.setup_score;
        const risk     = a.risk_assessment || a.entry_quality || '';
        const adjList  = a.adjustments || a.key_risks || [];
        verdictHtml = `<div class="pending-verdict">
          <span style="font-weight:700;color:${vColor}">${rec}</span>
          ${score != null ? `<span style="color:var(--muted)"> · Score ${score}/10</span>` : ''}
          ${risk ? `<span style="color:var(--muted)"> · ${escHtml(risk)}</span>` : ''}
          <div style="margin-top:6px;color:var(--muted);font-size:.8rem">${(() => {
            // summary may be a raw JSON string if the AI response was truncated
            let sumText = a.summary || '';
            if (sumText.trimStart().startsWith('{')) {
              try {
                const inner = JSON.parse(sumText);
                sumText = inner.entry_reason || inner.summary || inner.recommendation || '';
              } catch(_) {
                // truncated JSON — show a retry hint instead of raw text
                sumText = a.setup_quality?.label === 'Parse Error'
                  ? '⚠ Analysis was truncated — click AI Analysis to retry.'
                  : sumText.replace(/[{}"]/g, '').slice(0, 200);
              }
            }
            return escHtml(sumText);
          })()}</div>
          ${adjList.length ? `<div style="margin-top:8px;font-size:.78rem">${adjList.map(x=>`<div style="padding:3px 0;border-bottom:1px solid var(--border)">→ ${escHtml(x)}</div>`).join('')}</div>` : ''}
          ${a.key_risks?.length && !adjList.includes(a.key_risks[0]) ? `<div style="margin-top:6px;font-size:.75rem;color:var(--red)">${a.key_risks.map(r=>`<div>⚠ ${escHtml(r)}</div>`).join('')}</div>` : ''}
        </div>`;
      }
    } catch(e) {}
  }

  const waitingActions = lim.status === 'waiting' ? `
    <button class="btn btn-sm" style="background:linear-gradient(135deg,#6c63ff,#4fc3f7);color:#fff;border:none" onclick="analyzePendingLimit(${lim.id})">🤖 AI Analysis</button>
    ${!lim.call_id ? `<button class="btn btn-sm" style="background:rgba(108,99,255,.12);color:var(--accent);border:1px solid rgba(108,99,255,.3)" onclick="openLinkCallModal(${lim.id})">🔗 Link Call</button>` : ''}
    <button class="btn btn-sm btn-secondary" onclick="openLimitModal(${lim.id})">✏ Edit</button>
    <button class="btn btn-sm" style="background:rgba(38,217,107,.15);color:var(--accent3);border:1px solid rgba(38,217,107,.3)" onclick="markLimitTriggered(${lim.id})">✅ Mark Triggered</button>
    <button class="btn btn-sm btn-danger" onclick="cancelPendingLimit(${lim.id})">✕ Cancel</button>` :
    `<button class="btn btn-sm btn-danger" onclick="deleteLimit(${lim.id})">🗑 Delete</button>`;

  return `<div class="pending-card ${lim.status}" id="pending-card-${lim.id}">
    <div class="pending-card-header">
      <span style="font-size:1rem;font-weight:700">${escHtml(lim.symbol)}</span>
      <span class="badge ${dirClass}">${lim.direction}</span>
      <span class="${chipClass}">${chipLabel}</span>
      ${lim.bitget_order_id ? `<span style="font-size:.65rem;background:rgba(79,195,247,.12);color:var(--accent2);padding:2px 8px;border-radius:20px;font-weight:700">⚡ Bitget</span>` : ''}
      ${lim.call_id ? `<span style="font-size:.65rem;background:rgba(108,99,255,.12);color:var(--accent);padding:2px 8px;border-radius:20px;font-weight:700">📡 Linked Call #${lim.call_id}</span>` : ''}
      ${lim.analyst ? `<span style="font-size:.75rem;color:var(--muted)">${escHtml(lim.analyst)}</span>` : ''}
      <span id="prox-${lim.id}" style="display:none"></span>
      <div style="display:flex;gap:16px;align-items:center;margin-left:auto;flex-wrap:wrap">
        ${lim.limit_price ? `<div class="pos-stat"><div class="pos-stat-label">Limit</div><div class="pos-stat-val" style="color:var(--accent2)">${lim.limit_price}</div></div>` : ''}
        ${lim.sl_price    ? `<div class="pos-stat"><div class="pos-stat-label">SL</div><div class="pos-stat-val" style="color:var(--red)">${lim.sl_price}</div></div>` : ''}
        ${lim.tp1_price   ? `<div class="pos-stat"><div class="pos-stat-label">TP1</div><div class="pos-stat-val" style="color:var(--accent3)">${lim.tp1_price}</div></div>` : ''}
        ${lim.tp2_price   ? `<div class="pos-stat"><div class="pos-stat-label">TP2</div><div class="pos-stat-val" style="color:var(--accent3)">${lim.tp2_price}</div></div>` : ''}
        ${lim.size_usdt   ? `<div class="pos-stat"><div class="pos-stat-label">Size</div><div class="pos-stat-val" style="color:var(--yellow)">${fmtC(lim.size_usdt)} USDT</div></div>` : ''}
        ${lim.leverage    ? `<div class="pos-stat"><div class="pos-stat-label">Lev</div><div class="pos-stat-val">${lim.leverage}x</div></div>` : ''}
      </div>
    </div>
    ${(riskStr || rrStr || lim.notes || lim.triggered_at) ? `<div style="padding:0 18px 8px;font-size:.75rem;color:var(--muted)">
      ${riskStr}${rrStr}
      ${lim.notes ? `<span style="margin-left:8px">· ${escHtml(lim.notes)}</span>` : ''}
      ${lim.triggered_at ? `<span style="margin-left:8px">· Triggered: ${lim.triggered_at.slice(0,16)}</span>` : ''}
      <span style="margin-left:8px">· Added: ${(lim.created_at||'').slice(0,10)}</span>
    </div>` : ''}
    ${verdictHtml}
    ${lim.chart_png_b64 ? (() => {
      const trades = JSON.stringify([{
        dir:   lim.direction || 'Long',
        entry: lim.limit_price  || null,
        sl:    lim.sl_price     || null,
        tp1:   lim.tp1_price    || null,
        tp2:   lim.tp2_price    || null,
      }]);
      const chartUrl = `/chart?symbol=${encodeURIComponent(lim.symbol)}&timeframe=4H&trades=${encodeURIComponent(trades)}`;
      return `<div style="padding:0 18px 12px;position:relative;display:inline-block;max-width:680px;width:100%">
        <img src="data:image/png;base64,${lim.chart_png_b64}"
             style="width:100%;border-radius:8px;border:1px solid var(--border);display:block" alt="Setup chart">
        <button onclick="window.open('${chartUrl}','chart_${lim.symbol}','width=1060,height=680,resizable=yes,scrollbars=no,toolbar=no,menubar=no,location=no');event.stopPropagation()"
                style="position:absolute;top:8px;right:8px;background:rgba(15,17,23,.85);border:1px solid var(--border);
                       color:var(--text);border-radius:6px;padding:3px 9px;font-size:.72rem;cursor:pointer;
                       backdrop-filter:blur(4px)" title="Open interactive chart">↗ Pop Out</button>
      </div>`;
    })() : ''}
    <div id="pending-analyze-${lim.id}" style="display:none;padding:0 18px 12px"></div>
    <div style="display:flex;gap:8px;padding:10px 18px 14px;flex-wrap:wrap">${waitingActions}</div>
  </div>`;
}

function openLimitModal(limitId, prefill) {
  const fields = ['lm-symbol','lm-limit-price','lm-size-usdt','lm-sl-price','lm-tp1-price','lm-tp2-price','lm-analyst','lm-notes'];
  fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  document.getElementById('lm-leverage').value   = '10';
  document.getElementById('lm-direction').value  = 'Long';
  document.getElementById('limit-modal-id').value    = limitId || '';
  document.getElementById('limit-modal-call-id').value = (prefill && prefill.call_id) ? prefill.call_id : '';

  if (prefill) {
    if (prefill.symbol)        document.getElementById('lm-symbol').value        = prefill.symbol;
    if (prefill.direction)     document.getElementById('lm-direction').value     = prefill.direction;
    if (prefill.sl_price)      document.getElementById('lm-sl-price').value      = prefill.sl_price;
    if (prefill.tp1_price)     document.getElementById('lm-tp1-price').value     = prefill.tp1_price;
    if (prefill.tp2_price)     document.getElementById('lm-tp2-price').value     = prefill.tp2_price;
    if (prefill.entry_price)   document.getElementById('lm-limit-price').value   = prefill.entry_price;
    if (prefill.total_notional)document.getElementById('lm-size-usdt').value     = prefill.total_notional;
    if (prefill.analyst)       document.getElementById('lm-analyst').value       = prefill.analyst;
    if (prefill.leverage)      document.getElementById('lm-leverage').value      = prefill.leverage;
  }

  if (limitId && !prefill) {
    api('/api/limits?status=waiting').then(res => {
      const lim = (res.data||[]).find(l => l.id === limitId);
      if (!lim) return;
      document.getElementById('lm-symbol').value        = lim.symbol;
      document.getElementById('lm-direction').value     = lim.direction;
      document.getElementById('lm-limit-price').value   = lim.limit_price;
      if (lim.size_usdt)  document.getElementById('lm-size-usdt').value  = lim.size_usdt;
      if (lim.leverage)   document.getElementById('lm-leverage').value   = lim.leverage;
      if (lim.sl_price)   document.getElementById('lm-sl-price').value   = lim.sl_price;
      if (lim.tp1_price)  document.getElementById('lm-tp1-price').value  = lim.tp1_price;
      if (lim.tp2_price)  document.getElementById('lm-tp2-price').value  = lim.tp2_price;
      if (lim.analyst)    document.getElementById('lm-analyst').value    = lim.analyst;
      if (lim.notes)      document.getElementById('lm-notes').value      = lim.notes;
    });
  }
  document.getElementById('limit-modal').classList.add('open');
}

function closeLimitModal() {
  document.getElementById('limit-modal').classList.remove('open');
}

async function saveLimitOrder() {
  const limitId    = document.getElementById('limit-modal-id').value;
  const symbol     = document.getElementById('lm-symbol').value.trim().toUpperCase();
  const limitPrice = parseFloat(document.getElementById('lm-limit-price').value);
  if (!symbol || !limitPrice) { alert('Symbol and Limit Price are required.'); return; }

  const body = {
    symbol,
    direction:  document.getElementById('lm-direction').value,
    limit_price: limitPrice,
    size_usdt:  parseFloat(document.getElementById('lm-size-usdt').value)  || null,
    leverage:   parseInt(document.getElementById('lm-leverage').value)     || 10,
    sl_price:   parseFloat(document.getElementById('lm-sl-price').value)   || null,
    tp1_price:  parseFloat(document.getElementById('lm-tp1-price').value)  || null,
    tp2_price:  parseFloat(document.getElementById('lm-tp2-price').value)  || null,
    analyst:    document.getElementById('lm-analyst').value.trim(),
    notes:      document.getElementById('lm-notes').value.trim(),
    call_id:    document.getElementById('limit-modal-call-id').value || null,
  };

  const res = limitId
    ? await api('/api/limits/' + limitId, 'PATCH', body)
    : await api('/api/limits', 'POST', body);

  if (res.ok) {
    closeLimitModal();
    if (currentPage === 'pending') loadPendingLimits(currentLimitStatus);
  } else alert('Error: ' + res.error);
}

async function analyzePendingLimit(limitId) {
  const analyzeDiv = document.getElementById('pending-analyze-' + limitId);
  if (analyzeDiv) {
    analyzeDiv.style.display = '';
    analyzeDiv.innerHTML = '<div style="padding:12px;color:var(--muted);display:flex;align-items:center;gap:8px"><div class="spinner" style="width:18px;height:18px;border-width:2px;margin:0"></div> Analyzing with Claude…</div>';
  }
  const res = await api('/api/limits/' + limitId + '/analyze', 'POST');
  if (analyzeDiv) analyzeDiv.style.display = 'none';
  if (!res.ok) { alert('Analysis error: ' + res.error); return; }
  loadPendingLimits(currentLimitStatus);
}

async function markLimitTriggered(limitId) {
  if (!confirm('Mark this limit as triggered / filled?')) return;
  const res = await api('/api/limits/' + limitId, 'PATCH', { status: 'triggered' });
  if (res.ok) loadPendingLimits(currentLimitStatus);
  else alert('Error: ' + res.error);
}

async function cancelPendingLimit(limitId) {
  if (!confirm('Cancel this pending limit order?')) return;
  const res = await api('/api/limits/' + limitId, 'PATCH', { status: 'cancelled' });
  if (res.ok) loadPendingLimits(currentLimitStatus);
  else alert('Error: ' + res.error);
}

async function deleteLimit(limitId) {
  if (!confirm('Permanently delete this limit record?')) return;
  const res = await api('/api/limits/' + limitId, 'DELETE');
  if (res.ok) loadPendingLimits(currentLimitStatus);
  else alert('Error: ' + res.error);
}

document.getElementById('limit-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeLimitModal();
});

// ══════════════════════════════════════════════════════════════════════════════
// ANALYST LEADERBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function loadAnalystStats() {
  const res = await api('/api/calls/analyst-stats');
  const sec = document.getElementById('analyst-stats-section');
  const con = document.getElementById('analyst-stats-content');
  if (!res.ok || !res.data.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const medals = ['🥇','🥈','🥉'];

  function edgeBadge(score) {
    if (score == null) return '<span style="color:var(--muted);font-size:.75rem">need 3+ trades</span>';
    const color = score >= 65 ? 'var(--accent3)' : score >= 45 ? 'var(--yellow)' : 'var(--red)';
    const bg    = score >= 65 ? 'rgba(38,217,107,.15)' : score >= 45 ? 'rgba(255,179,0,.15)' : 'rgba(239,83,80,.15)';
    return `<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:.8rem;font-weight:800;background:${bg};color:${color}">${score}</span>`;
  }

  con.innerHTML = `
    <div class="table-card">
      <div style="font-size:.72rem;color:var(--muted);padding:8px 16px 4px">
        Sorted by Edge Score (0-100) · 50% trade win rate + 30% call outcome win rate + 20% TP1 hit rate · Needs 3+ closed trades to score
      </div>
      <table class="tbl">
        <thead><tr>
          <th>#</th><th>Analyst</th>
          <th title="Edge Score (0-100 composite)">Edge</th>
          <th title="Closed trades in journal">Trades</th>
          <th title="Journal win rate">Win %</th>
          <th title="Total realized PnL">Total PnL</th>
          <th title="Average PnL per trade">Avg PnL</th>
          <th title="Calls recorded as won vs SL hit">Call W/L</th>
          <th title="TP1 hit rate from recorded outcomes">TP1 Rate</th>
          <th title="How often you enter their calls">Entry Rate</th>
          <th title="Avg setup score">Score</th>
          <th title="Waiting limits">Pending</th>
        </tr></thead>
        <tbody>${res.data.map((a, i) => {
          const ranked = a.edge_score != null;
          const rowStyle = ranked && a.edge_score >= 65 ? 'background:rgba(38,217,107,.04)'
                         : ranked && a.edge_score < 45  ? 'background:rgba(239,83,80,.04)' : '';
          return `<tr style="${rowStyle}">
            <td style="color:var(--muted);font-size:.9rem">${medals[i] || (i+1)}</td>
            <td><strong>${escHtml(a.analyst)}</strong></td>
            <td>${edgeBadge(a.edge_score)}</td>
            <td>${a.trade_count || 0}</td>
            <td class="${a.win_rate >= 50 ? 'pos' : 'neg'}">${a.trade_count > 0 ? a.win_rate + '%' : '—'}</td>
            <td class="${pnlClass(a.total_pnl)}">${a.trade_count > 0 ? pnlSign(a.total_pnl) + fmtC(a.total_pnl) : '—'}</td>
            <td class="${pnlClass(a.avg_pnl)}">${a.trade_count > 0 ? pnlSign(a.avg_pnl) + fmtC(a.avg_pnl) : '—'}</td>
            <td>${a.call_win_rate != null ? `<span class="pos">${a.call_win_rate}%</span>` : '—'}</td>
            <td>${a.tp1_hit_rate != null ? `<span class="${a.tp1_hit_rate >= 50 ? 'pos' : 'neg'}">${a.tp1_hit_rate}%</span>` : '—'}</td>
            <td style="color:var(--muted)">${a.conv_rate != null ? a.conv_rate + '%' : '—'}</td>
            <td style="color:var(--muted)">${a.avg_setup_score != null ? a.avg_setup_score + '/10' : '—'}</td>
            <td>${a.pending_count > 0 ? `<span style="color:var(--accent2)">${a.pending_count}</span>` : '—'}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    </div>`;
}


// ══════════════════════════════════════════════════════════════════════════════
// MOBILE — compact limit cards (DOM-only, no innerHTML)
// ══════════════════════════════════════════════════════════════════════════════
// Valid statuses from routes/limits.py VALID_STATUSES allowlist:
const _VALID_LIMIT_STATUSES = new Set(['waiting','triggered','dismissed','expired','cancelled']);

function renderLimitCards(limits) {
    const container = document.getElementById('limits-cards');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!limits || !limits.length) {
        const msg = document.createElement('p');
        msg.className = 'muted';
        msg.style.padding = '16px';
        msg.textContent = 'No pending limits.';
        container.appendChild(msg);
        return;
    }

    limits.forEach(lim => {
        const card = document.createElement('div');
        card.className = 'limit-card';

        const hdr = document.createElement('div');
        hdr.className = 'limit-card-header';

        const symEl = document.createElement('span');
        symEl.className = 'limit-card-symbol';
        symEl.textContent = lim.symbol;  // exchange symbol, alphanumeric

        // status comes from VALID_STATUSES allowlist — safe to use as CSS class
        const rawStatus = _VALID_LIMIT_STATUSES.has(lim.status) ? lim.status : 'waiting';
        const statusEl = document.createElement('span');
        statusEl.className = 'limit-card-status ' + rawStatus;
        statusEl.textContent = rawStatus;

        hdr.appendChild(symEl);
        hdr.appendChild(statusEl);

        const dirRow = document.createElement('div');
        dirRow.className = 'scan-card-row';
        const dirLbl = document.createElement('span');
        dirLbl.className = 'lbl';
        dirLbl.textContent = 'Direction';
        const dirVal = document.createElement('span');
        dirVal.textContent = (lim.direction || '') + ' ' + (lim.leverage || '') + 'x';
        dirRow.appendChild(dirLbl);
        dirRow.appendChild(dirVal);

        const priceRow = document.createElement('div');
        priceRow.className = 'scan-card-row';
        const priceLbl = document.createElement('span');
        priceLbl.className = 'lbl';
        priceLbl.textContent = 'Limit price';
        const priceVal = document.createElement('span');
        priceVal.textContent = lim.limit_price ? parseFloat(lim.limit_price).toFixed(4) : '—';
        priceRow.appendChild(priceLbl);
        priceRow.appendChild(priceVal);

        const levels = document.createElement('div');
        levels.className = 'limit-card-levels';
        [
            ['SL',  lim.sl_price,  'pnl-neg'],
            ['TP1', lim.tp1_price, 'pnl-pos'],
            ['TP2', lim.tp2_price, 'pnl-pos'],
        ].forEach(([label, price, cls]) => {
            const lvl = document.createElement('div');
            lvl.className = 'lvl';
            const lbl = document.createElement('div');
            lbl.className = 'lbl';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.className = 'val ' + cls;
            val.textContent = price ? parseFloat(price).toFixed(4) : '—';
            lvl.appendChild(lbl);
            lvl.appendChild(val);
            levels.appendChild(lvl);
        });

        card.appendChild(hdr);
        card.appendChild(dirRow);
        card.appendChild(priceRow);
        card.appendChild(levels);
        container.appendChild(card);
    });
}
