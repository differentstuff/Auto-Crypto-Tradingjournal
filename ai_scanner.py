"""
ai_scanner.py — Proactive setup scanner.

Scans a watchlist of USDT-M futures for trade setups scored 6-10/10.

Three-stage pipeline:
  Stage 1 — Confluence filter (parallel, no AI):
             Computes multi-TF RSI/MACD/EMA/ADX signals for all symbols.
             Passes symbols with ≥ 2 signals aligned in one direction.

  Stage 2 — Technical quality gate (no AI, instant):
             Rejects severely overextended RSI, absent S/R structure, flat ADX.

  Stage 3 — AI scoring (parallel Claude calls, finalists only):
             Claude evaluates each finalist and returns scored setups 6-10/10
             with specific entry zone, SL, TP1, TP2 and rationale for each level.
             Setups below 6 are discarded.

Results cached for 30 minutes. Scan runs in a background thread.
"""

import copy
import json
import logging
import os
from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES
from constants import (MODEL, FAST_MODEL,
    SCANNER_MIN_SCORE, SCANNER_FULL_DETAIL_TOP_N, SCANNER_CACHE_TTL,
    SCANNER_MAX_WORKERS, PROMPT_CACHE_MIN_CHARS)
import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ai_client import send as ai_send
from database import db_conn
from trade_history import get_symbol_summary
from helpers import strip_fence, build_cached_messages
import chart_context
import market_context
import ai_rulebook

logger = logging.getLogger(__name__)
import gemini_client
import agent_orchestrator
import nansen_client

# ── Re-exports from sub-modules ────────────────────────────────────────────────
from scanner_watchlist import _BITGET_WATCHLIST, DEFAULT_WATCHLIST, _get_extended_watchlist
from scanner_criteria import (
    CRITERIA_DEFAULTS,
    _CRITERIA_DISABLED_LABELS,
    _disabled_criteria_block,
    _is_in_kill_zone,
    _annotate_kill_zone,
)
from scanner_prompts import (
    _build_prompt,
    _build_shared_prefix,
    _build_scanner_stable,
    _quick_score,
    _build_batch_prompt,
)
from scanner_stages import (
    _fetch_one,
    _stage2,
    _get_scan_macro_context,
    _apply_macro_cap,
    enrich_finalists_1h,
)

# ── Watchlist mutable state (kept here so tests can reset ai_scanner.BINANCE_WATCHLIST) ──
# Static symbol data lives in scanner_watchlist.py; the lazy-load cache lives here.
BINANCE_WATCHLIST: list = []
_binance_watchlist_loaded = False


def _get_default_watchlist() -> list:
    """Return merged Bitget+Binance watchlist, fetching Binance on first call."""
    global BINANCE_WATCHLIST, _binance_watchlist_loaded
    if not _binance_watchlist_loaded:
        _binance_watchlist_loaded = True
        try:
            import ccxt_client as _ccxt
            BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
        except Exception:
            BINANCE_WATCHLIST = []
    return list(dict.fromkeys(
        _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
    ))


# ── Scan state ─────────────────────────────────────────────────────────────────

_state: dict = {
    "status":          "idle",   # idle | running | completed | error
    "stage":           0,        # 0=idle, 1=confluence, 2=quality gate, 3=AI scoring
    "stage_label":     "",       # e.g. "Stage 1 — Confluence filter"
    "stage_detail":    "",       # e.g. "42 / 100 symbols processed"
    "stage_progress":  0,        # 0–100 within current stage
    "started_at":      None,
    "completed_at":    None,
    "duration_sec":    None,
    "setups":          [],
    "scanned":         0,
    "after_filter":    0,
    "error":           None,
    "min_score":       SCANNER_MIN_SCORE,
    "macro_ctx":       {},       # macro context fetched once per scan run
}
_state_lock   = threading.Lock()
_cancel_event = threading.Event()   # set to request cancellation; cleared on each new scan

# Completion hooks — called with the setups list when any scan finishes.
# Registered by scanner_scheduler so both manual and scheduled scans trigger TG.
# NOT fired on cancellation.
_completion_hooks: list = []


def register_completion_hook(fn) -> None:
    """Register a callable(setups: list) fired when any scan completes."""
    if fn not in _completion_hooks:
        _completion_hooks.append(fn)


def get_state() -> dict:
    with _state_lock:
        return copy.deepcopy(_state)


def cancel_scan() -> bool:
    """Request cancellation of the running scan. Returns True if a scan was running."""
    with _state_lock:
        if _state["status"] != "running":
            return False
    _cancel_event.set()
    return True


def _update(**kwargs):
    with _state_lock:
        _state.update(kwargs)


# ── Stage 1 wrapper — injects _update for live progress ───────────────────────

def _stage1(symbols: list, min_score: int = SCANNER_MIN_SCORE) -> list:
    """Return [(symbol, ctx, conf, direction)] with enough aligned signals.
    Emits live progress via _update() as futures complete."""
    from scanner_stages import _stage1 as _stage1_impl
    return _stage1_impl(symbols, min_score, _update_fn=_update)


# ── Stage 3: AI scoring ─────────────────────────────────────────────────────────

def _score_finalists_with_agents(finalists: list, conn,
                                 min_score: int = SCANNER_MIN_SCORE,
                                 macro_ctx: dict = None) -> list:
    """
    Run the agent pipeline (DataCollector → Interpreter → Sentiment →
    Reviewer → TradePrep) for each finalist. Replaces the inline Sonnet batch call.

    finalists: list of (sym, ctx, conf, direction, quick_score, rationale) tuples
    Returns list of setup dicts compatible with the scanner output format.
    """
    import agent_data_collector
    import agent_data_interpreter
    import agent_market_sentiment
    import agent_data_reviewer
    import agent_orchestrator

    macro = macro_ctx or {}
    results = []
    for sym, ctx, conf, direction, quick_score, rationale in finalists:
        try:
            collected = agent_data_collector.run({
                "symbol": sym, "direction": direction, "timeframes": ["1H", "4H", "1D"],
            })
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_i = ex.submit(agent_data_interpreter.run, {"collected": collected})
                f_s = ex.submit(agent_market_sentiment.run,
                                {"symbol": sym, "direction": direction,
                                 "collected": collected})
            interpreted = f_i.result()
            sentiment   = f_s.result()
            reviewed = agent_data_reviewer.run({
                "interpreted": interpreted, "symbol": sym,
                "direction": direction, "setup_type": "scanner",
            }, conn)
            prep = agent_orchestrator.run_scanner_prep(
                sym, direction, collected, interpreted, reviewed, sentiment, conn,
            )
            score = prep.get("setup_score", 0)
            # Apply macro regime cap before threshold check
            score, macro_warnings = _apply_macro_cap(float(score), macro)
            if macro_warnings:
                logger.info("macro cap applied to %s: %s", sym, "; ".join(macro_warnings))
            if score < min_score:
                continue
            entry_p = float(prep.get("entry_price", 0) or 0)
            if not entry_p:
                # Fallback: use current price from already-computed 4H chart context
                ema_4h = ctx.get("4H", {}).get("indicators", {}).get("ema") or {}
                entry_p = float(ema_4h.get("current_price") or 0)
            urgency = ("Now" if score >= 9 else
                       "1-4h" if score >= 8 else
                       "Today" if score >= 7 else "1-3 days")
            setup = {
                "_symbol":        sym,
                "symbol":         sym,
                "direction":      direction,
                "setup_score":    score,
                "setup_label":    prep.get("_model", ""),
                "entry_zone":     {"low": entry_p, "high": entry_p,
                                   "rationale": "Agent pipeline entry level"},
                "sl_price":       prep.get("sl_price", 0),
                "tp1_price":      prep.get("tp1_price", 0),
                "tp2_price":      prep.get("tp2_price", 0),
                "rr_ratio":       prep.get("rr_ratio", 0),
                "key_conditions": prep.get("key_conditions", []),
                "chart_png_b64":  prep.get("chart_png_b64", ""),
                "summary":        " · ".join(prep.get("key_conditions", [])[:2]),
                "_quick_score":   quick_score,
                "_rationale":     rationale,
                "confluence_summary": conf.get("label", ""),
                "chart_pattern":  prep.get("chart_pattern") or None,
                "urgency":        urgency,
                "timeframe":      "Multi-TF (1D/4H/1H)",
            }
            if macro_warnings:
                setup["macro_warnings"] = macro_warnings
            results.append(setup)
        except Exception as e:
            logger.warning("agent scoring failed for %s: %s", sym, e)
    return results


# ── Symbol history helper ───────────────────────────────────────────────────────


# ── Background scan thread ─────────────────────────────────────────────────────

def _check_cancel() -> bool:
    """Returns True if cancellation was requested. Updates state and logs."""
    if _cancel_event.is_set():
        _update(status="cancelled", completed_at=time.time())
        logger.info("[Scanner] Scan cancelled by user request")
        return True
    return False


def _scan_thread(symbols: list, min_score: int = SCANNER_MIN_SCORE, criteria: dict = None):
    cr = criteria or CRITERIA_DEFAULTS
    t0 = time.time()
    _cancel_event.clear()   # reset any previous cancellation request

    # Fetch macro context once at the start — passed to all scoring stages
    macro_ctx = _get_scan_macro_context()
    _update(macro_ctx=macro_ctx)
    if macro_ctx.get("vix") or macro_ctx.get("macro_risk"):
        logger.info("macro ctx: VIX=%s regime=%s macro_risk=%s event=%s in %sh",
                    macro_ctx.get("vix"), macro_ctx.get("regime"),
                    macro_ctx.get("macro_risk"), macro_ctx.get("next_event"),
                    macro_ctx.get("hours_until"))

    _update(
        status="running", started_at=t0, error=None, setups=[], scanned=0, after_filter=0,
        min_score=min_score,
        stage=1, stage_label="Stage 1 — Confluence filter",
        stage_detail=f"Fetching multi-TF data for {len(symbols)} symbols…",
        stage_progress=0,
    )

    try:
        # Stage 1 — confluence filter (emits per-symbol progress internally)
        candidates = _stage1(symbols, min_score)
        passed1 = len(candidates)
        if _check_cancel(): return

        # Stage 2 — technical quality gate
        _update(
            stage=2, stage_label="Stage 2 — Quality gate",
            stage_detail=f"{passed1} symbols passed confluence → applying technical filters…",
            stage_progress=0,
        )
        finalists = _stage2(candidates, min_score, criteria=cr)
        _update(scanned=len(symbols), after_filter=len(finalists),
                stage_detail=f"{passed1} passed confluence · {len(finalists)} passed quality gate",
                stage_progress=100)
        if _check_cancel(): return

        if not finalists:
            _update(status="completed", completed_at=time.time(),
                    duration_sec=round(time.time() - t0, 1),
                    stage=0, stage_label="", stage_detail="No candidates passed the quality gate")
            return

        # Shared context for all finalists
        try:
            mkt_ctx = market_context.get_market_context(
                [s for s, _, _, _ in finalists[:5]]
            )
            mkt_str = market_context.format_for_prompt(mkt_ctx)
        except Exception as e:
            logger.warning("market context fetch failed in scan: %s", e)
            mkt_str = ""

        # Append BTC market regime
        try:
            regime = market_context.get_btc_regime()
            regime_map = {"bull": "📈 BTC is in a BULL regime (EMA50 > EMA200) — favour long setups",
                          "bear": "📉 BTC is in a BEAR regime (EMA50 < EMA200) — favour short setups",
                          "range": "↔ BTC is in a RANGE/transition — both directions valid, be selective"}
            mkt_str = (mkt_str + "\n" if mkt_str else "") + f"BTC MARKET REGIME: {regime_map[regime]}"
        except Exception as e:
            logger.warning("scoring failed: %s", e)
        with db_conn() as conn:
            rulebook_str = ai_rulebook.get_rulebook_for_prompt(conn)
            histories = {s: get_symbol_summary(s, conn) for s, _, _, _ in finalists}

        # Nansen smart money signals — one API call for all finalists combined
        nansen_signals = {}
        if nansen_client.is_configured():
            _update(stage_detail="Fetching Nansen smart money signals…")
            try:
                finalist_syms  = [s for s, _, _, _ in finalists]
                nansen_signals = nansen_client.get_signals_for_symbols(finalist_syms)
                active = sum(1 for v in nansen_signals.values() if v.get("ok"))
                print(f"[Nansen] {active}/{len(finalist_syms)} finalists have smart money signal", flush=True)
            except Exception as e:
                print(f"[Nansen] Signal fetch failed: {e}", flush=True)

        # Enrich finalists with 1H chart data before AI scoring stages
        _update(stage_detail="Fetching 1H data for finalists…")
        finalists = enrich_finalists_1h(finalists)
        if _check_cancel(): return

        # Stage 3a — Quick score all finalists with Haiku (cheap pre-filter pass)
        quick_threshold = max(min_score - 1, 4)
        _update(
            stage=3, stage_label="Stage 3a — Haiku quick-score",
            stage_detail=f"Fast-scoring {len(finalists)} finalist{'s' if len(finalists)!=1 else ''} with Haiku…",
            stage_progress=0,
        )
        shared_prefix = _build_shared_prefix(mkt_str, rulebook_str, min_score, criteria=cr)
        quick_results = []
        qs_done = [0]
        qs_total = len(finalists)
        with ThreadPoolExecutor(max_workers=10) as ex:
            fq = {
                ex.submit(_quick_score, sym, ctx, conf, dir_, shared_prefix, quick_threshold): (sym, ctx, conf, dir_)
                for sym, ctx, conf, dir_ in finalists
            }
            for f in as_completed(fq):
                if _cancel_event.is_set():
                    ex.shutdown(wait=False, cancel_futures=True)
                    _check_cancel()
                    return
                sym, ctx, conf, dir_ = fq[f]
                qs_done[0] += 1
                _update(
                    stage_detail  = f"Haiku scoring: {qs_done[0]} / {qs_total} symbols",
                    stage_progress= int(qs_done[0] / qs_total * 100),
                )
                r = f.result()
                if r is not None:
                    quick_results.append((sym, ctx, conf, dir_, r["score"], r.get("reason", "")))

        if _check_cancel(): return

        # Sort by quick score, take top N for expensive full-detail pass
        quick_results.sort(key=lambda x: -x[4])
        top_finalists  = quick_results[:SCANNER_FULL_DETAIL_TOP_N]
        rest_finalists = quick_results[SCANNER_FULL_DETAIL_TOP_N:]

        # Stage 3b — Full detail with Sonnet: single batched call for all top-N
        _update(
            stage_label   = "Stage 3b — Sonnet full analysis",
            stage_detail  = f"Batch-scoring top {len(top_finalists)} setup{'s' if len(top_finalists)!=1 else ''} with Sonnet…",
            stage_progress= 0,
        )
        with db_conn() as conn:
            setups = _score_finalists_with_agents(top_finalists, conn, min_score=min_score,
                                                  macro_ctx=macro_ctx)
        _update(stage_progress=100)

        # Add non-top-N setups with Haiku score + one-sentence rationale
        for sym, ctx, conf, direction, score, reason in rest_finalists:
            inds  = ctx.get("4H", {}).get("indicators", {})
            price = inds.get("ema", {}).get("current_price")
            urg   = ("Now" if score >= 9 else
                     "1-4h" if score >= 8 else
                     "Today" if score >= 7 else "1-3 days")
            setups.append({
                "symbol":            sym,
                "direction":         direction,
                "setup_score":       score,
                "setup_label":       "Quick score only",
                "why_this_score":    reason or "No rationale (Haiku quick-score pass)",
                "quick_score_only":  True,
                "confluence":        conf.get("label", ""),
                "current_price":     price,
                "chart_pattern":     None,
                "urgency":           urg,
                "timeframe":         "4H",
            })

        # Attach Nansen smart money signal to each setup
        for setup in setups:
            sym = setup.get("_symbol") or setup.get("symbol", "")
            ns  = nansen_signals.get(sym, {})
            if ns.get("ok"):
                setup["nansen"] = {
                    "direction":   ns["direction"],
                    "strength":    ns["strength"],
                    "netflow_usd": ns["netflow_usd"],
                    "nof_traders": ns["nof_traders"],
                    "chain":       ns.get("chain", ""),
                }

        setups.sort(key=lambda x: -x.get("setup_score", 0))
        if _check_cancel(): return

        # Stage 3c — Gemini consensus for top-5 finalists (parallel, non-blocking)
        if setups and gemini_client.is_configured():
            _update(stage_detail="Stage 3c — Gemini consensus scoring top 5…")
            # Build symbol → chart_ctx map from top_finalists (sym, ctx, conf, dir_, score, reason)
            ctx_map = {sym: ctx for sym, ctx, _conf, _dir, _sc, _r in top_finalists}
            try:
                setups = agent_orchestrator.add_gemini_consensus(setups, ctx_map, max_setups=5)
            except Exception as e:
                logger.warning("Gemini consensus step failed: %s", e)

        _update(
            status="completed", setups=setups,
            completed_at=time.time(), duration_sec=round(time.time() - t0, 1),
            stage=0, stage_label="",
            stage_detail=f"{len(setups)} setup{'s' if len(setups)!=1 else ''} found in {round(time.time()-t0,1)}s",
        )

        # Fire completion hooks (registered by scanner_scheduler for TG + entry_watcher)
        for hook in list(_completion_hooks):
            try:
                hook(setups)
            except Exception as hook_err:
                logger.warning("Completion hook failed: %s", hook_err)

    except Exception as e:
        logger.exception("Scan thread failed")
        _update(status="error", error="Scan failed — check server logs",
                completed_at=time.time(), duration_sec=round(time.time() - t0, 1))


# ── Public API ─────────────────────────────────────────────────────────────────

def start_scan(symbols: list = None, min_score: int = SCANNER_MIN_SCORE,
               criteria: dict = None) -> bool:
    """
    Start a background scan. Returns False if already running or results are still
    fresh (< SCANNER_CACHE_TTL seconds old) AND the min_score hasn't changed.
    """
    with _state_lock:
        if _state["status"] == "running":
            return False
        completed_at   = _state.get("completed_at")
        score_unchanged = _state.get("min_score", SCANNER_MIN_SCORE) == min_score
        if completed_at and (time.time() - completed_at) < SCANNER_CACHE_TTL and score_unchanged:
            return False  # still fresh with same threshold

    syms = symbols or _get_extended_watchlist(500)
    cr   = criteria or CRITERIA_DEFAULTS
    t = threading.Thread(target=_scan_thread, args=(syms, min_score, cr), daemon=True)
    t.start()
    return True


def force_scan(symbols: list = None, min_score: int = SCANNER_MIN_SCORE,
               criteria: dict = None) -> bool:
    """Start a scan regardless of cache TTL. Returns False if already running."""
    with _state_lock:
        if _state["status"] == "running":
            return False
    syms = symbols or _get_extended_watchlist(500)
    cr   = criteria or CRITERIA_DEFAULTS
    t = threading.Thread(target=_scan_thread, args=(syms, min_score, cr), daemon=True)
    t.start()
    return True
