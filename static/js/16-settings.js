// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS — Exchange credential management
// All innerHTML in this file is built from server-returned numeric/status values
// and static template strings — no user-supplied content is ever interpolated.
// ══════════════════════════════════════════════════════════════════════════════

async function loadSettings() {
  const el = document.getElementById('settings-content');
  if (!el) return;
  el.textContent = 'Loading…';

  const res = await api('/api/settings/exchanges');
  if (!res.ok) { el.textContent = 'Failed to load settings.'; return; }

  const { bitget, blofin, env_file_exists } = res.data;

  let blofinSyncHtml = '';
  if (blofin.configured) {
    const sr = await api('/api/settings/blofin/status');
    if (sr.ok) {
      const s  = sr.data;
      const eq = s.account_equity != null ? parseFloat(s.account_equity).toFixed(2) + ' USDT' : '—';
      const lr = s.last_run  || '—';
      const nr = s.next_run  || '—';
      const stateLabel = s.running ? '🔄 Running' : (s.last_error ? '❌ Error' : '✅ Idle');
      blofinSyncHtml = `
        <div class="settings-sync-box">
          <div style="font-weight:600;color:var(--text);margin-bottom:8px">Blofin Sync Status</div>
          <div class="settings-sync-grid">
            <div>Status: <b>${stateLabel}</b></div>
            <div>Equity: <b style="color:var(--accent2)">${eq}</b></div>
            <div>Last run: ${lr}</div>
            <div>Next run: ${nr}</div>
          </div>
          ${s.last_error ? `<div class="settings-error">Error: ${escHtml(s.last_error)}</div>` : ''}
          <button class="btn-primary" onclick="triggerBlofinSync()" style="margin-top:10px">Sync Now</button>
        </div>`;
    }
  }

  const envBanner = `
    <div class="settings-env-banner ${env_file_exists ? 'ok' : 'warn'}">
      ${env_file_exists ? '✅ .env file found — credentials load from file'
                        : '⚠️ No .env file — set credentials below or use environment variables'}
    </div>`;

  // Build the full page
  el.innerHTML = `
    ${envBanner}
    ${_exchangeCard('bitget', 'Bitget', '🟡', bitget)}
    <div style="height:16px"></div>
    ${_exchangeCard('blofin', 'Blofin', '🔵', blofin)}
    ${blofinSyncHtml}
    <div class="settings-section-title" style="margin-top:32px;color:var(--muted);font-size:.8rem">
      💡 Use the <b>All / Bitget / Blofin</b> pills in the top bar to filter all statistics, charts, and analytics by exchange.
    </div>

    <div class="settings-section-title" style="margin-top:28px">📲 Telegram Alerts</div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden">
      <div id="tg-scanner-row" style="padding:14px 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)">
        <div>
          <div style="font-size:.85rem;font-weight:600">⭐ Setup Scanner Alerts</div>
          <div style="font-size:.75rem;color:var(--muted);margin-top:2px">Notify when scanner finds new 6+/10 setups (every 30 min)</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <span id="tg-scanner-label" style="font-size:.78rem;color:var(--muted)">Loading…</span>
          <button id="tg-scanner-btn" onclick="toggleTelegramType('scanner')" style="padding:5px 16px;font-size:.8rem;border-radius:6px;border:1px solid var(--border);cursor:pointer;background:var(--bg);color:var(--text)">…</button>
        </div>
      </div>
      <div id="tg-monitor-row" style="padding:14px 18px;display:flex;align-items:center;justify-content:space-between">
        <div>
          <div style="font-size:.85rem;font-weight:600">👁 Position Monitor Alerts</div>
          <div style="font-size:.75rem;color:var(--muted);margin-top:2px">Notify when an open position needs attention (risk ≥7 or action ≠ Hold)</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <span id="tg-monitor-label" style="font-size:.78rem;color:var(--muted)">Loading…</span>
          <button id="tg-monitor-btn" onclick="toggleTelegramType('monitor')" style="padding:5px 16px;font-size:.8rem;border-radius:6px;border:1px solid var(--border);cursor:pointer;background:var(--bg);color:var(--text)">…</button>
        </div>
      </div>
    </div>

    <div class="settings-section-title" style="margin-top:28px">🤖 AI Token Usage (last 7 days)</div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem">
        <thead>
          <tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border)">
            <th style="padding:4px 8px">Module</th>
            <th style="padding:4px 8px">Model</th>
            <th style="padding:4px 8px">Input tok</th>
            <th style="padding:4px 8px">Output tok</th>
            <th style="padding:4px 8px">Est. cost</th>
          </tr>
        </thead>
        <tbody id="token-usage-body">
          <tr><td colspan="5" style="color:var(--muted);padding:8px">Loading…</td></tr>
        </tbody>
      </table>
      <div id="token-usage-total" style="margin-top:8px;font-size:.76rem;color:var(--muted)"></div>
    </div>`;

  _injectSettingsCSS();
  loadTokenUsage();
  _loadTelegramToggle();

  // Append Bitget backfill box after Bitget card (only if configured)
  if (bitget.configured) {
    _appendBitgetBackfillBox(el);
  }
}

function _appendBitgetBackfillBox(parentEl) {
  // Find the first settings-card (Bitget) and insert backfill box right after
  const firstCard = parentEl.querySelector('.settings-card');
  if (!firstCard) return;
  const box = document.createElement('div');
  box.className = 'settings-sync-box';
  const title = document.createElement('div');
  title.style.cssText = 'font-weight:600;color:var(--text);margin-bottom:8px';
  title.textContent = 'Bitget — Historical Backfill';
  const desc = document.createElement('div');
  desc.style.cssText = 'font-size:.78rem;color:var(--muted);margin-bottom:8px';
  desc.textContent = 'Fetch up to 5000 historical trades from Bitget. Useful for first-time setup or recovering missing trades.';
  const btn = document.createElement('button');
  btn.className = 'btn-secondary';
  btn.style.marginTop = '8px';
  btn.textContent = 'Backfill from Exchange (last 5000 trades)';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Backfilling...';
    try {
      const r = await fetch('/api/sync/backfill', { method: 'POST' });
      const d = await r.json();
      if (d.ok) {
        notify('Backfill complete: ' + d.data.inserted + ' new trades inserted', 'ok');
      } else {
        notify(d.error || 'Backfill failed', 'err');
      }
    } catch (e) {
      notify('Backfill request failed', 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Backfill from Exchange (last 5000 trades)';
    }
  });
  box.appendChild(title);
  box.appendChild(desc);
  box.appendChild(btn);
  firstCard.parentNode.insertBefore(box, firstCard.nextSibling);
}

async function _loadTelegramToggle() {
  const res = await api('/api/settings/telegram');
  if (!res.ok) return;
  _renderTelegramRow('scanner', res.data.scanner_enabled);
  _renderTelegramRow('monitor', res.data.monitor_enabled);
}

function _renderTelegramRow(type, enabled) {
  const label = document.getElementById('tg-' + type + '-label');
  const btn   = document.getElementById('tg-' + type + '-btn');
  if (!label || !btn) return;
  label.textContent     = enabled ? '● Enabled' : '○ Disabled';
  label.style.color     = enabled ? 'var(--accent3)' : 'var(--red)';
  btn.textContent       = enabled ? 'Disable' : 'Enable';
  btn.style.background  = enabled ? 'rgba(239,83,80,.12)' : 'rgba(38,217,107,.12)';
  btn.style.color       = enabled ? 'var(--red)' : 'var(--accent3)';
  btn.style.borderColor = enabled ? 'var(--red)' : 'var(--accent3)';
}

async function toggleTelegramType(type) {
  const label = document.getElementById('tg-' + type + '-label');
  const currentlyEnabled = label && label.textContent.includes('Enabled');
  const res = await api('/api/settings/telegram', 'POST', { type, enabled: !currentlyEnabled });
  if (!res.ok) { notify('Failed to update Telegram setting', 'danger'); return; }
  _renderTelegramRow(type, res.data.enabled);
  const name = type === 'scanner' ? 'Scanner alerts' : 'Monitor alerts';
  notify(name + ' ' + (res.data.enabled ? 'enabled' : 'disabled'), 'success');
}


function _exchangeCard(id, name, icon, cfg) {
  const statusColor = cfg.configured ? 'var(--accent3)' : 'var(--muted)';
  const statusLabel = cfg.configured ? '● Connected' : '○ Not configured';
  const keyPreview  = cfg.api_key_preview || '—';
  const ppTick      = cfg.has_passphrase  ? '&nbsp;·&nbsp; Passphrase: ✓' : '';

  return `
    <div class="settings-card">
      <div class="settings-card-header">
        <div class="settings-card-title">${icon} ${name}</div>
        <div style="font-size:.8rem;color:${statusColor}">${statusLabel}</div>
      </div>

      <div class="settings-card-meta">
        API Key: <code>${keyPreview}</code>${ppTick}
      </div>

      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <button class="btn-secondary" id="test-btn-${id}" onclick="testExchangeConn('${id}')">Test Connection</button>
        <button class="btn-ghost"    id="edit-btn-${id}" onclick="toggleCredForm('${id}')">Edit Credentials</button>
        <span id="test-result-${id}" style="font-size:.78rem"></span>
      </div>

      <div id="cred-form-${id}" class="settings-cred-form" style="display:none">
        <div class="settings-cred-grid">
          <div>
            <div class="settings-input-label">API Key</div>
            <input id="cred-key-${id}" type="password" placeholder="Paste API key…" class="settings-input">
          </div>
          <div>
            <div class="settings-input-label">Secret Key</div>
            <input id="cred-secret-${id}" type="password" placeholder="Paste secret key…" class="settings-input">
          </div>
        </div>
        <div style="margin-bottom:10px">
          <div class="settings-input-label">Passphrase (leave blank if none)</div>
          <input id="cred-phrase-${id}" type="password" placeholder="Optional" class="settings-input" style="width:50%">
        </div>
        <button class="btn-primary" onclick="saveCredentials('${id}')">Save</button>
        <span id="save-result-${id}" style="margin-left:10px;font-size:.78rem"></span>
      </div>
    </div>`;
}


function _injectSettingsCSS() {
  if (document.getElementById('settings-css')) return;
  const s = document.createElement('style');
  s.id = 'settings-css';
  s.textContent = `
    .settings-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin-bottom:0}
    .settings-card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
    .settings-card-title{font-weight:700;font-size:.95rem}
    .settings-card-meta{font-size:.8rem;color:var(--muted);margin-bottom:12px}
    .settings-card-meta code{font-family:monospace;color:var(--text);background:var(--bg3);padding:1px 6px;border-radius:3px}
    .settings-cred-form{margin-top:16px;padding-top:16px;border-top:1px solid var(--border)}
    .settings-cred-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
    .settings-input{width:100%;padding:7px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.82rem}
    .settings-input-label{font-size:.72rem;color:var(--muted);margin-bottom:4px}
    .settings-env-banner{padding:10px 14px;border-radius:8px;font-size:.82rem;color:var(--muted);margin-bottom:20px}
    .settings-env-banner.ok{background:rgba(38,217,107,.08);border:1px solid rgba(38,217,107,.2)}
    .settings-env-banner.warn{background:rgba(239,83,80,.08);border:1px solid rgba(239,83,80,.2)}
    .settings-sync-box{margin-top:16px;padding:14px 16px;background:var(--bg3);border-radius:8px;font-size:.82rem;color:var(--muted)}
    .settings-sync-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px}
    .settings-error{color:var(--red);font-size:.78rem;margin-top:4px}
    .settings-section-title{font-weight:700;font-size:.9rem;color:var(--text);margin-bottom:10px}
    .btn-primary{padding:7px 18px;background:var(--accent);color:#fff;border:none;border-radius:5px;font-size:.8rem;cursor:pointer}
    .btn-secondary{padding:6px 14px;background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:5px;font-size:.78rem;cursor:pointer}
    .btn-ghost{padding:6px 14px;background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:5px;font-size:.78rem;cursor:pointer}
    .exch-filter-btn{padding:6px 14px;border-radius:5px;font-size:.78rem;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--muted);transition:.15s}
    .exch-filter-btn:hover,.exch-filter-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}
  `;
  document.head.appendChild(s);
}


async function testExchangeConn(exchange) {
  const btn = document.getElementById(`test-btn-${exchange}`);
  const out = document.getElementById(`test-result-${exchange}`);
  btn.disabled = true;
  btn.textContent = 'Testing…';
  out.textContent = '';
  const res = await api('/api/settings/test-connection', 'POST', { exchange });
  btn.disabled    = false;
  btn.textContent = 'Test Connection';
  if (res.ok) {
    out.style.color = 'var(--accent3)';
    out.textContent = '✓ ' + res.data.message;
  } else {
    out.style.color = 'var(--red)';
    out.textContent = '✗ ' + (res.error || 'Failed');
  }
}


function toggleCredForm(exchange) {
  const form = document.getElementById(`cred-form-${exchange}`);
  if (!form) return;
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}


async function saveCredentials(exchange) {
  const key    = document.getElementById(`cred-key-${exchange}`)?.value.trim();
  const secret = document.getElementById(`cred-secret-${exchange}`)?.value.trim();
  const phrase = document.getElementById(`cred-phrase-${exchange}`)?.value.trim() || '';
  const out    = document.getElementById(`save-result-${exchange}`);

  if (!key || !secret) {
    out.style.color = 'var(--red)';
    out.textContent = 'API key and secret key are required';
    return;
  }
  out.style.color = 'var(--muted)';
  out.textContent = 'Saving…';
  const res = await api('/api/settings/credentials', 'POST',
    { exchange, api_key: key, secret_key: secret, passphrase: phrase });
  if (res.ok) {
    out.style.color = 'var(--accent3)';
    out.textContent = '✓ Saved';
    setTimeout(loadSettings, 800);
  } else {
    out.style.color = 'var(--red)';
    out.textContent = '✗ ' + (res.error || 'Save failed');
  }
}


async function triggerBlofinSync() {
  const res = await api('/api/settings/blofin/sync', 'POST');
  if (res.ok) {
    notify(`Blofin sync: ${res.data.positions} new trade(s) imported`, 'ok');
    setTimeout(loadSettings, 800);
  } else {
    notify(res.error || 'Blofin sync failed', 'err');
  }
}


async function loadTokenUsage() {
  const el = document.getElementById('token-usage-body');
  if (!el) return;
  const res = await api('/api/token-usage?days=7');
  if (!res.ok) { el.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">No data yet</td></tr>'; return; }
  const d = res.data;
  const rows = (d.by_module || []).map(r => `
    <tr>
      <td>${r.module}</td>
      <td style="color:var(--muted);font-size:.75rem">${r.model.split('-').slice(-2).join('-')}</td>
      <td>${(r.total_input || 0).toLocaleString()}</td>
      <td>${(r.total_output || 0).toLocaleString()}</td>
      <td style="color:var(--accent3)">$${r.est_cost_usd}</td>
    </tr>`).join('');
  el.innerHTML = rows || '<tr><td colspan="5" style="color:var(--muted)">No calls in last 7 days</td></tr>';
  const tot = d.totals || {};
  document.getElementById('token-usage-total').textContent =
    `7-day total: ${(tot.total_input||0).toLocaleString()} in + ${(tot.total_output||0).toLocaleString()} out tokens — est. $${d.est_cost_usd} USD`;
}


// Exchange filter is now global in the sync bar (01-utils.js setGlobalExchange).
// getExchangeFilter() is kept as an alias for backward compatibility.
function getExchangeFilter() { return _globalExchange; }
