
// ══════════════════════════════════════════════════════════════════════════════
// AI ADVISOR
// ══════════════════════════════════════════════════════════════════════════════
async function runAI() {
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('ai-loading').style.display = 'block';
  document.getElementById('ai-results').style.display = 'none';

  try {
    const res = await api('/api/ai/analyze', 'POST', exchFilters());
    if (!res.ok) throw new Error(res.error);
    const d = res.data;
    renderAI(d);
    document.getElementById('ai-last-run').textContent =
      `Last analyzed: ${new Date().toLocaleString()} · ${d._input_tokens} in / ${d._output_tokens} out tokens`;
  } catch(e) {
    document.getElementById('ai-results').innerHTML =
      `<div class="upload-result error">Error: ${e.message}</div>`;
    document.getElementById('ai-results').style.display = 'block';
  } finally {
    document.getElementById('ai-loading').style.display = 'none';
    document.getElementById('btn-analyze').disabled = false;
  }
}

function renderAI(d) {
  const scoreColor = d.score?.value >= 7 ? 'var(--accent3)' :
                     d.score?.value >= 5 ? 'var(--yellow)' : 'var(--red)';
  let html = `
    <div class="ai-score">
      <div class="ai-score-num" style="color:${scoreColor}">${d.score?.value ?? '—'}/10</div>
      <div class="ai-score-label">${d.score?.label ?? ''} Trader</div>
    </div>
    <div class="ai-overall">${d.overall_status || ''}</div>`;

  if (d.strengths?.length) {
    html += `<div class="ai-section"><h3>💪 Strengths</h3>` +
      d.strengths.map(s => `
        <div class="ai-item">
          <div class="ai-item-title">${s.title}</div>
          <div class="ai-item-detail">${s.detail}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.weaknesses?.length) {
    html += `<div class="ai-section"><h3>⚠️ Areas to Improve</h3>` +
      d.weaknesses.map(w => `
        <div class="ai-item">
          <div class="ai-item-title">${w.title}</div>
          <div class="ai-item-detail">${w.detail}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.recommendations?.length) {
    html += `<div class="ai-section"><h3>🎯 Action Plan</h3>` +
      d.recommendations.map(r => `
        <div class="ai-item">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <span class="priority-${r.priority?.toLowerCase()}">${r.priority}</span>
            <span class="ai-item-title">${r.title}</span>
          </div>
          <div class="ai-item-detail"><strong>Action:</strong> ${r.action}</div>
          <div class="ai-item-detail"><strong>Expected:</strong> ${r.expected_impact}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.symbol_insights?.length) {
    html += `<div class="ai-section"><h3>📊 Symbol Insights</h3>` +
      d.symbol_insights.map(s => `
        <div class="ai-item">
          <div class="ai-item-title">${s.symbol}</div>
          <div class="ai-item-detail">${s.insight}</div>
        </div>`).join('') + `</div>`;
  }

  if (d.risk_management) {
    html += `<div class="ai-section"><h3>🛡 Risk Management</h3>
      <div class="ai-item-detail" style="line-height:1.7">${d.risk_management}</div></div>`;
  }

  if (d.mindset_note) {
    html += `<div class="ai-section" style="border-color:var(--accent);background:rgba(108,99,255,.05)">
      <h3>🧠 Mindset</h3>
      <div class="ai-item-detail" style="line-height:1.7">${d.mindset_note}</div></div>`;
  }

  document.getElementById('ai-results').innerHTML = html;
  document.getElementById('ai-results').style.display = 'block';
}
