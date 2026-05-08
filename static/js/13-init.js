// ══════════════════════════════════════════════════════════════════════════════
// showPage extension for live pages
const _origShowPage = showPage;
showPage = function(name) {
  const extras = ['live', 'trades', 'calls', 'pending', 'charts', 'scanner', 'hindsight', 'settings'];
  if (extras.includes(name)) {
    document.querySelectorAll('.page-view').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    document.getElementById('nav-' + name).classList.add('active');
    currentPage = name;
    if (name === 'live')    { pollSyncStatus(); loadTelegramStatus(); }
    if (name === 'calls')   { loadCallEquity(); loadSavedCalls(); loadAnalystStats(); loadPredictionAccuracy(); }
    if (name === 'pending') { loadBitgetOrders(); loadPendingLimits('waiting'); }
    if (name === 'charts')  { _initExplorerTfBtns(); }
    if (name === 'scanner')   { loadScanner(); }
    if (name === 'hindsight') { loadHindsight(); }
    if (name === 'settings')  { loadSettings(); }
    if (name === 'trades') {
      loadLiveTrades();
      // Auto-refresh every 30s while on this page
      if (liveTradesInterval) clearInterval(liveTradesInterval);
      liveTradesInterval = setInterval(() => {
        if (currentPage === 'trades') loadLiveTrades();
      }, 30000);
    }
    return;
  }
  // Stop live trades refresh when leaving the page
  if (liveTradesInterval) { clearInterval(liveTradesInterval); liveTradesInterval = null; }
  _origShowPage(name);
};

// ── Initial load ───────────────────────────────────────────────────────────────
loadDashboard();
// Poll sync status every 30s so the sync bar stays current
pollSyncStatus();
syncPolling = setInterval(pollSyncStatus, 30000);
// Attach searchable symbol pickers, then load symbols so pickers have data
['m-symbol', 'lm-symbol', 'explorer-symbol'].forEach(_attachSymbolPicker);
loadSymbols();          // trade-history symbols → fast fallback in picker
_loadExchangeSymbols(); // full exchange list → replaces fallback when ready
