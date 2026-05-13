import os

# ── Anthropic models ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL                  = "claude-sonnet-4-6"
FAST_MODEL             = "claude-haiku-4-5-20251001"

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
CHART_CACHE_TTL        = 600    # 10 min — candle cache in chart_context
SCANNER_CACHE_TTL      = 1800   # 30 min — scanner result cache
MARKET_CACHE_TTL       = 300    # 5 min  — Fear & Greed / funding rates
NANSEN_CACHE_TTL       = 1800   # 30 min — Nansen smart money cache

# ── Scanner pipeline ──────────────────────────────────────────────────────────
SCANNER_MIN_SCORE         = 6
SCANNER_FULL_DETAIL_TOP_N = 12
SCANNER_MAX_WORKERS       = 4   # ThreadPoolExecutor — tuned to Pi 4-core CPU

# ── Position sizing ───────────────────────────────────────────────────────────
DEFAULT_LEVERAGE         = 10
DEFAULT_RISK_PCT         = 1.0
DEFAULT_DCA_RISK_PCT     = 2.0
FALLBACK_EQUITY_USDT     = 1000.0  # only when ALL exchange equity calls fail

# ── Prompt budget ─────────────────────────────────────────────────────────────
MAX_CONTEXT_CHARS        = 5_600
PROMPT_CACHE_MIN_CHARS   = 4_096   # Anthropic cache_control minimum

# ── Chart S/R & trendline tolerance ──────────────────────────────────────────
PRICE_TOLERANCE          = 0.004   # 0.4% — S/R clustering and trendline validation
