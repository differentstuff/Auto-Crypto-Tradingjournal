
// ══════════════════════════════════════════════════════════════════════════════
// IMPORT
// ══════════════════════════════════════════════════════════════════════════════
function handleDrop(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.remove('dragover');
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
}

async function handleFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  document.getElementById('upload-result').innerHTML =
    '<div class="upload-result" style="background:var(--bg2);border:1px solid var(--border)">Uploading…</div>';
  try {
    const r = await fetch('/api/import', { method: 'POST', body: fd });
    const res = await r.json();
    if (res.ok) {
      const d = res.data;
      document.getElementById('upload-result').innerHTML =
        `<div class="upload-result success">
          ✅ Import complete!
          Positions: ${d.positions??0} · Orders: ${d.orders??0} · Transactions: ${d.transactions??0}
        </div>`;
      loadImportLog();
    } else {
      document.getElementById('upload-result').innerHTML =
        `<div class="upload-result error">❌ ${res.error}</div>`;
    }
  } catch(e) {
    document.getElementById('upload-result').innerHTML =
      `<div class="upload-result error">❌ ${e.message}</div>`;
  }
}

async function loadImportLog() {
  const res = await api('/api/import/status');
  if (!res.ok) return;
  document.getElementById('import-log-tbody').innerHTML = res.data.length === 0
    ? '<tr><td colspan="4" style="text-align:center;color:var(--muted)">No imports yet</td></tr>'
    : res.data.map(r => `
        <tr>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${r.filename}</td>
          <td>${r.file_type}</td>
          <td>${r.rows_imported}</td>
          <td>${r.imported_at?.slice(0,16)}</td>
        </tr>`).join('');
}

// ── Close modals on overlay click ──────────────────────────────────────────────
document.getElementById('trade-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
document.getElementById('notes-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeNotesModal();
});
