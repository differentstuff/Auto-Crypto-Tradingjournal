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
          ${s.last_error ? `<div class="settings-error">Error: ${s.last_error}</div>` : ''}
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
    <div class="settings-section-title" style="margin-top:32px">Journal Filter by Exchange</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="exch-filter-btn${_exchangeFilter==='all'?' active':''}" onclick="setExchangeFilter('all',this)">All Exchanges</button>
      <button class="exch-filter-btn${_exchangeFilter==='bitget'?' active':''}" onclick="setExchangeFilter('bitget',this)">Bitget only</button>
      <button class="exch-filter-btn${_exchangeFilter==='blofin'?' active':''}" onclick="setExchangeFilter('blofin',this)">Blofin only</button>
    </div>`;

  _injectSettingsCSS();
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
  const res = await api('/api/settings/test-connection', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ exchange }),
  });
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
  const res = await api('/api/settings/credentials', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ exchange, api_key: key, secret_key: secret, passphrase: phrase }),
  });
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
  const res = await api('/api/settings/blofin/sync', { method: 'POST' });
  if (res.ok) {
    notify(`Blofin sync: ${res.data.positions} new trade(s) imported`, 'ok');
    setTimeout(loadSettings, 800);
  } else {
    notify(res.error || 'Blofin sync failed', 'err');
  }
}


// ── Exchange filter for journal ────────────────────────────────────────────────
let _exchangeFilter = localStorage.getItem('journalExchangeFilter') || 'all';

function setExchangeFilter(val, btn) {
  _exchangeFilter = val;
  localStorage.setItem('journalExchangeFilter', val);
  document.querySelectorAll('.exch-filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  if (typeof loadJournal === 'function') loadJournal();
}

function getExchangeFilter() {
  return _exchangeFilter;
}
