
// ══════════════════════════════════════════════════════════════════════════════
// LIVE SYNC + TELEGRAM ALERTS
// ══════════════════════════════════════════════════════════════════════════════
let syncPolling = null;

async function pollSyncStatus() {
  try {
    const res = await api('/api/sync/status');
    if (!res.ok) return;
    const s = res.data;

    const dot   = document.getElementById('sync-dot');
    const label = document.getElementById('sync-label');
    const eq    = document.getElementById('sync-equity');

    if (s.running) {
      dot.className   = 'sync-dot syncing';
      label.textContent = 'Syncing with Bitget…';
    } else if (s.last_error) {
      dot.className   = 'sync-dot error';
      label.textContent = 'Sync error — ' + s.last_error.slice(0, 60);
    } else if (s.last_run) {
      dot.className   = 'sync-dot';
      label.textContent = 'Live · Last sync: ' + s.last_run;
    } else {
      dot.className   = 'sync-dot syncing';
      label.textContent = 'First sync starting…';
    }

    if (s.account_equity) {
      const val = parseFloat(s.account_equity).toFixed(2);
      eq.textContent = '⚡ ' + val + ' USDT';
      // Live page cards
      document.getElementById('live-equity').textContent    = val + ' USDT';
      document.getElementById('live-last').textContent      = s.last_run || '—';
      document.getElementById('live-next').textContent      = 'Next: ' + (s.next_run || '—');
    }
    if (s.available_balance) {
      document.getElementById('live-available').textContent =
        parseFloat(s.available_balance).toFixed(2) + ' USDT';
    }
  } catch(e) {}
}

async function triggerSync(fromLivePage = false) {
  const btn = fromLivePage
    ? document.getElementById('live-btn-sync')
    : document.getElementById('btn-sync');
  const msg = document.getElementById('live-sync-msg');

  btn.disabled = true;
  document.getElementById('btn-sync').disabled = true;
  if (msg) msg.textContent = 'Syncing…';
  document.getElementById('sync-dot').className = 'sync-dot syncing';
  document.getElementById('sync-label').textContent = 'Syncing with Bitget…';

  try {
    const res = await api('/api/sync', 'POST');
    if (res.ok) {
      const d = res.data;
      const total = (d.positions||0) + (d.orders||0) + (d.bills||0);
      if (msg) msg.textContent = `✅ Sync complete — ${d.positions} new positions, ${d.orders} orders, ${d.bills} bills`;

      // Update result table on live page
      document.getElementById('live-result-tbody').innerHTML = `
        <tr><td style="color:var(--muted)">New positions</td><td class="pos">+${d.positions}</td></tr>
        <tr><td style="color:var(--muted)">New orders</td><td>+${d.orders}</td></tr>
        <tr><td style="color:var(--muted)">New bills</td><td>+${d.bills}</td></tr>
        <tr><td style="color:var(--muted)">Synced at</td><td>${d.synced_at}</td></tr>`;

      // Refresh dashboard if new data arrived
      if (total > 0 && currentPage === 'dashboard') loadDashboard();
      if (total > 0 && currentPage === 'journal')   journalLoad(journalPage);
    } else {
      if (msg) msg.textContent = '❌ ' + res.error;
    }
  } catch(e) {
    if (msg) msg.textContent = '❌ ' + e.message;
  } finally {
    btn.disabled = false;
    document.getElementById('btn-sync').disabled = false;
    pollSyncStatus();
  }
}

async function loadTelegramStatus() {
  const res = await api('/api/telegram/status');
  if (!res.ok) return;
  const d = res.data;
  const line = document.getElementById('telegram-status-line');
  const btn  = document.getElementById('btn-telegram-test');
  if (!line) return;

  if (d.configured) {
    line.innerHTML = `<span style="color:var(--accent3)">✅ Telegram configured</span> — `
      + `Scanner runs every <strong>${d.interval_min} min</strong>, `
      + `first scan in <strong>${d.first_delay_min} min</strong> after startup`;
    if (btn) btn.style.display = '';
  } else {
    line.innerHTML = `<span style="color:var(--muted)">⚠ Telegram not configured</span> — `
      + `add <code>TELEGRAM_BOT_TOKEN</code> and <code>TELEGRAM_CHAT_ID</code> to <code>.env</code>`;
    if (btn) btn.style.display = 'none';
  }
}

async function sendTelegramTest() {
  const btn = document.getElementById('btn-telegram-test');
  const res_el = document.getElementById('telegram-test-result');
  if (btn) btn.disabled = true;
  if (res_el) res_el.textContent = 'Sending…';
  const res = await api('/api/telegram/test', 'POST');
  if (res_el) res_el.textContent = res.ok ? '✅ Sent — check your Telegram' : '❌ ' + (res.error || 'failed');
  if (btn) btn.disabled = false;
}
