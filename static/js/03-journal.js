
// ══════════════════════════════════════════════════════════════════════════════
// JOURNAL
// ══════════════════════════════════════════════════════════════════════════════
async function loadSymbols() {
  const res = await api('/api/symbols');
  if (!res.ok) return;
  symbolList = res.data;
  const sel = document.getElementById('j-symbol');
  sel.innerHTML = '<option value="">All Symbols</option>' +
    symbolList.map(s => `<option>${s}</option>`).join('');
}

async function journalLoad(page) {
  journalPage = page;
  const exchFilter = _globalExchange || 'all';
  const params = new URLSearchParams({
    page,
    per_page: 50,
    search:    document.getElementById('j-search').value,
    symbol:    document.getElementById('j-symbol').value,
    direction: document.getElementById('j-direction').value,
    pnl_side:  document.getElementById('j-pnl').value,
    setup:     document.getElementById('j-setup').value,
    date_from: document.getElementById('j-from').value,
    date_to:   document.getElementById('j-to').value,
  });
  if (exchFilter && exchFilter !== 'all') params.set('exchange', exchFilter);
  const res = await api('/api/positions?' + params);
  if (!res.ok) return;
  const { positions, total, pages } = res.data;

  document.getElementById('journal-count').textContent = `${total} trade${total !== 1 ? 's' : ''}`;
  document.getElementById('journal-tbody').innerHTML = positions.map(t => `
    <tr onclick="openNotesModal(${t.id},'${escHtml(t.notes||'')}','${escHtml(t.tags||'')}','${escHtml(t.analyst||'')}','${escHtml(t.setup_type||'')}','${escHtml(t.execution_grade||'')}','${escHtml(t.execution_grade_reason||'')}',${t.call_id||'null'})"
        style="cursor:pointer" title="Click to edit">
      <td>
        <strong>${t.symbol}</strong>
        ${t.exchange && t.exchange !== 'bitget' ? `<span style="font-size:.62rem;padding:1px 5px;border-radius:3px;background:rgba(79,195,247,.15);color:rgba(79,195,247,.9);margin-left:4px">${t.exchange}</span>` : ''}
      </td>
      <td><span class="badge ${t.direction.toLowerCase()}">${t.direction}</span></td>
      <td>${t.open_time?.slice(0,16)}</td>
      <td>${t.close_time?.slice(0,16)}</td>
      <td>${durFmt(t.duration_minutes)}</td>
      <td>${t.entry_price != null ? fmt(t.entry_price) : '—'}</td>
      <td>${t.close_price != null ? fmt(t.close_price) : '—'}</td>
      <td>${fmtC(t.size_usdt)}</td>
      <td class="${pnlClass(t.realized_pnl)}">${pnlSign(t.realized_pnl)}${fmtC(t.realized_pnl)}</td>
      <td class="neg">${fmtC(t.total_fees)}</td>
      <td onclick="event.stopPropagation()" style="white-space:nowrap">
        ${t.execution_grade
          ? `<span class="grade-badge grade-${t.execution_grade}" title="${escHtml(t.execution_grade_reason||'')}">${t.execution_grade}</span>`
          : ''}
        <button class="btn btn-sm" style="padding:3px 8px;font-size:.7rem;background:rgba(108,99,255,.15);color:var(--accent);border:1px solid rgba(108,99,255,.3)"
          onclick="gradePosition(${t.id}, this)">⚡ Grade</button>
      </td>
      <td style="font-size:.78rem;color:var(--accent2)">${t.analyst ? '📡 '+escHtml(t.analyst) : '<span style="color:var(--muted)">—</span>'}</td>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:.78rem">
        ${t.setup_type ? '<span style="color:var(--accent2);margin-right:4px">'+escHtml(t.setup_type)+'</span>' : ''}${t.notes || (t.tags ? '🏷 ' + t.tags : '')}</td>
    </tr>`).join('');

  // Pagination
  const pg = document.getElementById('journal-pagination');
  if (pages <= 1) { pg.innerHTML = ''; return; }
  let html = '';
  if (page > 1) html += `<button class="page-btn" onclick="journalLoad(${page-1})">← Prev</button>`;
  const start = Math.max(1, page - 2);
  const end   = Math.min(pages, page + 2);
  for (let i = start; i <= end; i++) {
    html += `<button class="page-btn${i===page?' active':''}" onclick="journalLoad(${i})">${i}</button>`;
  }
  if (page < pages) html += `<button class="page-btn" onclick="journalLoad(${page+1})">Next →</button>`;
  pg.innerHTML = html;
}

function journalReset() {
  ['j-search','j-from','j-to'].forEach(id => document.getElementById(id).value = '');
  ['j-symbol','j-direction','j-pnl','j-setup'].forEach(id => document.getElementById(id).value = '');
  journalLoad(1);
}

function escHtml(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Add Trade Modal ────────────────────────────────────────────────────────────
function openAddModal() {
  document.getElementById('modal-title').textContent = 'Add Manual Trade';
  ['m-symbol','m-open','m-close','m-entry','m-exit','m-size','m-pnl','m-fees','m-notes','m-tags'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('m-setup').value = '';
  document.getElementById('trade-modal').classList.add('open');
}
function closeModal() { document.getElementById('trade-modal').classList.remove('open'); }

async function saveTrade() {
  const body = {
    symbol:       document.getElementById('m-symbol').value.trim().toUpperCase(),
    direction:    document.getElementById('m-direction').value,
    open_time:    document.getElementById('m-open').value.trim(),
    close_time:   document.getElementById('m-close').value.trim(),
    entry_price:  parseFloat(document.getElementById('m-entry').value) || 0,
    close_price:  parseFloat(document.getElementById('m-exit').value) || 0,
    size_usdt:    parseFloat(document.getElementById('m-size').value) || 0,
    realized_pnl: parseFloat(document.getElementById('m-pnl').value) || 0,
    total_fees:   parseFloat(document.getElementById('m-fees').value) || 0,
    margin_mode:  document.getElementById('m-margin').value,
    setup_type:   document.getElementById('m-setup').value,
    notes:        document.getElementById('m-notes').value,
    tags:         document.getElementById('m-tags').value,
  };
  if (!body.symbol || !body.open_time || !body.close_time) {
    alert('Symbol, open time and close time are required.');
    return;
  }
  const res = await api('/api/positions', 'POST', body);
  if (res.ok) { closeModal(); journalLoad(journalPage); }
  else alert('Error: ' + res.error);
}

// ── Edit Notes Modal ───────────────────────────────────────────────────────────
function openNotesModal(id, notes, tags, analyst, setupType, grade, gradeReason, callId) {
  document.getElementById('edit-id').value      = id;
  document.getElementById('edit-analyst').value = analyst || '';
  document.getElementById('edit-setup').value   = setupType || '';
  document.getElementById('edit-notes').value   = notes;
  document.getElementById('edit-tags').value    = tags;
  document.getElementById('edit-call-id').value = callId || '';

  const gradeBox = document.getElementById('edit-grade-box');
  if (grade) {
    document.getElementById('edit-grade-badge').textContent  = grade;
    document.getElementById('edit-grade-badge').className    = `grade-badge grade-${grade}`;
    document.getElementById('edit-grade-reason').textContent = gradeReason || '';
    gradeBox.style.display = 'block';
  } else {
    gradeBox.style.display = 'none';
  }
  document.getElementById('notes-modal').classList.add('open');
}
function closeNotesModal() { document.getElementById('notes-modal').classList.remove('open'); }

async function saveNotes() {
  const id     = document.getElementById('edit-id').value;
  const callId = document.getElementById('edit-call-id').value.trim();
  const body   = {
    analyst:    document.getElementById('edit-analyst').value.trim(),
    notes:      document.getElementById('edit-notes').value,
    tags:       document.getElementById('edit-tags').value,
    setup_type: document.getElementById('edit-setup').value,
    call_id:    callId ? parseInt(callId) : null,
  };
  const res = await api('/api/positions/' + id, 'PUT', body);
  if (res.ok) { closeNotesModal(); journalLoad(journalPage); }
  else alert('Error: ' + res.error);
}

async function gradePosition(id, btn) {
  const origText = btn.textContent;
  btn.textContent = '…';
  btn.disabled    = true;
  const res = await api('/api/positions/' + id + '/grade', 'POST');
  btn.disabled = false;
  if (res.ok) {
    btn.textContent = origText;
    // Update grade badge in the same cell
    const cell = btn.parentElement;
    let badge = cell.querySelector('.grade-badge');
    if (!badge) {
      badge = document.createElement('span');
      cell.insertBefore(badge, btn);
    }
    badge.className   = `grade-badge grade-${res.data.grade}`;
    badge.textContent = res.data.grade;
    badge.title       = res.data.reason || '';
  } else {
    btn.textContent = origText;
    alert('Grading failed: ' + (res.error || 'unknown error'));
  }
}

async function deleteTrade() {
  const id = document.getElementById('edit-id').value;
  if (!confirm('Delete this trade? This cannot be undone.')) return;
  const res = await api('/api/positions/' + id, 'DELETE');
  if (res.ok) { closeNotesModal(); journalLoad(journalPage); }
  else alert('Error: ' + res.error);
}
