
// ══════════════════════════════════════════════════════════════════════════════
// LIVE TRADES — Call Match + Targets Panel (split from 08-live.js v2.1)
// ══════════════════════════════════════════════════════════════════════════════

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
        <button class="btn btn-primary btn-sm" onclick="confirmMatch(${call.id}, '${key}', ${pos.id || 'null'}, '${pos.exchange || 'bitget'}')">✅ Yes, this is that trade</button>
        <button class="btn btn-secondary btn-sm" onclick="dismissMatch(${call.id})">✗ Not this trade</button>
      </div>
    </div>`;
  }).join('');
}

async function confirmMatch(callId, key, positionId, exchange) {
  const res = await api('/api/calls/' + callId + '/confirm-match', 'POST', {
    position_id: positionId || null,
    exchange: exchange || 'bitget',
  });
  if (!res.ok) {
    notify('Could not confirm match — ' + (res.error || 'server error'), 'err');
    return;
  }
  document.getElementById('match-banner-' + callId)?.remove();
  const savedRes = await api('/api/calls/saved');
  if (savedRes.ok) {
    const call = savedRes.data.find(c => c.id === callId);
    if (call) liveCallMatches[key] = call;
  }
  renderPositionCards(livePositionsCache);
  notify('Call linked — will auto-close when position closes', 'ok');
}

async function dismissMatch(callId) {
  await api('/api/calls/' + callId + '/dismiss', 'POST');
  document.getElementById('match-banner-' + callId)?.remove();
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
    const dist = ((p - mark) / mark * 100 * dir);
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
