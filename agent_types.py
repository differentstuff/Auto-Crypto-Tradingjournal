"""
agent_types.py — TypedDict contracts for all specialized agents.

Single source of truth for all input/output shapes. Import from here
rather than from individual agent files to avoid circular imports.
"""
from __future__ import annotations
from typing import TypedDict


class CollectorInput(TypedDict):
    symbol: str
    direction: str       # "Long" | "Short"
    timeframes: list     # e.g. ["4H", "1D"]


class CollectorResult(TypedDict):
    symbol: str
    candles: dict        # {"4H": pd.DataFrame, "1D": pd.DataFrame}
    funding_rate: dict   # {rate, rate_pct, direction, high, ok}
    open_interest: dict  # {oi_coins, oi_usd_m, change_24h_pct, trend, ok}
    long_short: dict     # {long_pct, short_pct, bias, ok}
    fear_greed: dict     # {value, classification, ok}
    fred_macro: dict     # {fed_rate, cpi, m2_b, t10y, ok}
    nansen: dict         # {signal, label, smart_money_bias} or {}
    grok: dict           # {text, weight} or {}
    fetched_at: float    # unix timestamp


class InterpreterInput(TypedDict):
    collected: CollectorResult


class InterpreterResult(TypedDict):
    symbol: str
    by_timeframe: dict   # {tf: indicators_dict} — raw output of compute_all_indicators()
    sr_levels: list      # [{price, type, strength, touches, recency_score}]
    confluence_score: dict  # {score, max, bullish, bearish, label, details}
    trend_direction: str    # "bullish" | "bearish" | "neutral"
    momentum_bias: str      # "strong" | "moderate" | "weak" | "conflicted"
    prompt_text: str        # compact ~400-char summary


class SentimentInput(TypedDict):
    symbol: str
    direction: str
    collected: CollectorResult


class SentimentResult(TypedDict):
    macro_bias: str         # "bullish" | "neutral" | "bearish"
    sentiment_score: float  # 0–10
    funding_bias: str       # "longs_paying" | "shorts_paying" | "neutral"
    crowd_position: str     # "majority_long" | "majority_short" | "balanced"
    contra_signal: bool     # True when crowd opposes trade direction by >65%
    key_factors: list       # ["F&G 82 — Extreme Greed", ...]
    grok_summary: str       # Grok text or ""
    prompt_text: str        # compact summary for injection


class ReviewerInput(TypedDict):
    interpreted: InterpreterResult
    symbol: str
    direction: str
    setup_type: str          # "breakout" | "reversal" | "continuation" | "range" | ""


class ReviewerResult(TypedDict):
    signal_quality: float    # 0–10
    warnings: list           # ["ADX 18 — no clear trend", ...]
    backtest_context: str    # from analytics.get_backtest_context()
    kpis: dict               # {win_rate_pct, avg_win, avg_loss, profit_factor, streak}
    symbol_history: dict     # from trade_history.get_symbol_summary()
    rubric: str              # setup-type scoring rubric


class TradePrepInput(TypedDict):
    collected: CollectorResult
    interpreted: InterpreterResult
    reviewed: ReviewerResult
    sentiment: SentimentResult
    call_text: str
    account_equity: float
    setup_type: str


class TradePrepResult(TypedDict):
    setup_score: int
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    rr_ratio: float
    key_conditions: list
    pattern_warnings: list
    sizing_hint: str
    cot_reasoning: str
    gemini_score: int
    consensus: dict
    raw_json: dict
    chart_png_b64: str       # base64 PNG of annotated chart, "" if not generated
    _model: str
    _cached_tokens: int


class RiskInput(TypedDict):
    trade_prep: TradePrepResult
    account_equity: float
    open_positions: list


class RiskResult(TypedDict):
    approved: bool
    position_size_usdt: float
    margin_usdt: float
    risk_pct: float
    atr_sl_valid: bool
    correlation_warning: str
    max_risk_hit: bool
    kelly_fraction: float
    warnings: list
    sizing_breakdown: dict


class MonitorInput(TypedDict):
    position: dict               # live position from bitget_client
    original_prep: dict          # TradePrepResult or {} if not available
    interpreted: InterpreterResult
    sentiment: SentimentResult


class MonitorResult(TypedDict):
    action: str                  # "Hold" | "Adjust SL" | "Partial Close" | "Close Now"
    action_reason: str
    risk_rating: int             # 1–10
    alert_level: str             # "info" | "warning" | "critical"
    tp_recommendation: dict      # {price, rationale}
    sl_recommendation: dict      # {price, rationale}
    key_risks: list
    summary: str
    _symbol: str
    _checked_at: float


class AnalysisResult(TypedDict):
    # from TradePrepResult
    setup_score: int
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    rr_ratio: float
    key_conditions: list
    pattern_warnings: list
    cot_reasoning: str
    gemini_score: int
    consensus: dict
    raw_json: dict
    chart_png_b64: str
    # from RiskResult
    risk_approved: bool
    risk_verdict_json: str
    position_size_usdt: float
    margin_usdt: float
    kelly_fraction: float
    # from SentimentResult
    macro_bias: str
    contra_signal: bool
    sentiment_score: float
    # from ReviewerResult
    signal_quality: float
    reviewer_warnings: list
    # pipeline metadata
    error: str
    degraded: bool
