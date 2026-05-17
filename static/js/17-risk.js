/* 17-risk.js -- Risk analytics dashboard tab */

function loadRiskDashboard() {
    loadVarPanel();
    loadCorrelationPanel();
    loadAttributionPanel();
    loadKellyPanel();
    loadAlphaDecayPanel();
}

async function loadVarPanel() {
    const el = document.getElementById('risk-var');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/var');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = 'No open positions or insufficient price history.';
            return;
        }
        const v = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(2,1fr);gap:12px';
        [
            ['95% VaR (1-day)', '$' + v.var_95_usd.toLocaleString('en-US', {maximumFractionDigits:0}), 'pnl-neg'],
            ['99% VaR (1-day)', '$' + v.var_99_usd.toLocaleString('en-US', {maximumFractionDigits:0}), 'pnl-neg'],
            ['95% VaR % of Notional', v.var_95_pct + '%', ''],
            ['Portfolio Notional',    '$' + v.total_notional.toLocaleString('en-US', {maximumFractionDigits:0}), ''],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:12px;border-radius:6px';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:11px;color:var(--text-muted,#888);text-transform:uppercase';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:18px;font-weight:700;margin-top:4px';
            val.textContent = value;
            if (cls) val.className = cls;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        el.appendChild(grid);
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:8px';
        note.textContent = v.sample_days + ' days of historical simulation data used';
        el.appendChild(note);
    } catch(e) { if (el) el.textContent = 'VaR unavailable.'; }
}

async function loadCorrelationPanel() {
    const el = document.getElementById('risk-correlation');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/correlation');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = (d.data && d.data.reason) || 'Need 2+ open positions.';
            return;
        }
        const c = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        if (c.high_risk_pairs && c.high_risk_pairs.length > 0) {
            const warn = document.createElement('div');
            warn.style.cssText = 'background:#3a1a1a;border-left:3px solid #ef5350;padding:10px;border-radius:4px;margin-bottom:10px;font-size:13px';
            const pairList = c.high_risk_pairs.map(p => p.symbol_a + '/' + p.symbol_b + ' (r=' + p.correlation + ')').join(', ');
            warn.textContent = 'High correlation, same direction: ' + pairList;
            el.appendChild(warn);
        }
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Symbol A', 'Symbol B', 'Correlation', 'Risk'].forEach(h => {
            const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        (c.matrix || []).slice(0, 10).forEach(row => {
            const tr = tbody.insertRow();
            const corr = row.correlation;
            const riskLabel = Math.abs(corr) > 0.80 ? 'Very High' : Math.abs(corr) > 0.60 ? 'High' : Math.abs(corr) > 0.40 ? 'Medium' : 'Low';
            const riskClass = Math.abs(corr) > 0.70 ? 'pnl-neg' : '';
            [row.symbol_a, row.symbol_b, corr.toFixed(3), riskLabel].forEach((val, i) => {
                const td = tr.insertCell(); td.textContent = val;
                if (i >= 2 && riskClass) td.className = riskClass;
            });
        });
        el.appendChild(tbl);
    } catch(e) { if (el) el.textContent = 'Correlation unavailable.'; }
}

async function loadAttributionPanel() {
    const el = document.getElementById('risk-attribution');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/attribution?days=90');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = 'No attributed trade history (need BTC price data).';
            return;
        }
        const a = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:12px';
        [
            ['Total P&L',       '$' + a.total_pnl.toFixed(2), a.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Alpha (Skill)',   '$' + a.alpha_pnl.toFixed(2), a.alpha_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Beta (BTC Move)', '$' + a.beta_pnl.toFixed(2),  a.beta_pnl  >= 0 ? 'pnl-pos' : 'pnl-neg'],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:12px;border-radius:6px;text-align:center';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:11px;color:var(--text-muted,#888);text-transform:uppercase;margin-bottom:4px';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:18px;font-weight:700';
            val.className = cls;
            val.textContent = value;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        el.appendChild(grid);
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:8px';
        note.textContent = a.alpha_pct + '% of P&L is alpha (skill). ' + a.sample_size + ' trades in last 90 days.';
        el.appendChild(note);
    } catch(e) { if (el) el.textContent = 'Attribution unavailable.'; }
}

async function loadKellyPanel() {
    const el = document.getElementById('risk-kelly');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/kelly');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = (d.data && d.data.reason) || 'Need trade history with setup scores.';
            return;
        }
        while (el.firstChild) el.removeChild(el.firstChild);
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Score', 'Trades', 'Win%', 'Avg Win', 'Avg Loss', 'Kelly', 'Recommended %'].forEach(h => {
            const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        (d.data.buckets || []).forEach(b => {
            const tr = tbody.insertRow();
            [b.score_range, b.trade_count, b.win_rate + '%',
             '$' + b.avg_win_usd.toFixed(1), '$' + b.avg_loss_usd.toFixed(1),
             b.kelly_full_pct + '%', b.recommended_size_pct + '%'].forEach((val, i) => {
                const td = tr.insertCell(); td.textContent = val;
                if (i === 6) { td.style.fontWeight = '700'; if (b.recommended_size_pct > 10) td.className = 'pnl-pos'; }
            });
        });
        el.appendChild(tbl);
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:8px';
        note.textContent = 'Half-Kelly applied. Hard cap: 20% of capital.';
        el.appendChild(note);
    } catch(e) { if (el) el.textContent = 'Kelly unavailable.'; }
}

async function loadAlphaDecayPanel() {
    const el = document.getElementById('risk-alpha-decay');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/alpha-decay');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = (d.data && d.data.reason) || 'No execution lag data yet. Sync trades from scanner to build this.';
            return;
        }
        const a = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        if (a.edge_decays) {
            const warn = document.createElement('div');
            warn.style.cssText = 'background:#1a2a3a;border-left:3px solid #64b5f6;padding:10px;border-radius:4px;margin-bottom:10px;font-size:13px';
            warn.textContent = 'Edge decay detected (r=' + (a.correlation || 0).toFixed(3) + '). Entering faster would likely improve returns.';
            el.appendChild(warn);
        }
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Entry Lag', 'Trades', 'Avg P&L', 'Win Rate'].forEach(h => {
            const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        (a.lag_buckets || []).forEach(b => {
            const tr = tbody.insertRow();
            [b.lag_range, b.trade_count,
             (b.avg_pnl >= 0 ? '+' : '') + '$' + b.avg_pnl.toFixed(2),
             b.win_rate + '%'].forEach((val, i) => {
                const td = tr.insertCell(); td.textContent = val;
                if (i === 2) td.className = b.avg_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
            });
        });
        el.appendChild(tbl);
    } catch(e) { if (el) el.textContent = 'Alpha decay unavailable.'; }
}
