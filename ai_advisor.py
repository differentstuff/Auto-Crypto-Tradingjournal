"""
ai_advisor.py — Claude-powered trading improvement recommendations.

Builds a rich prompt from aggregated trading stats and sends it to
claude-sonnet-4-6 via the Anthropic SDK. Returns structured JSON
with sections for strengths, weaknesses, and specific recommendations.

The full stats dict (from analytics.py) is serialized into the prompt so
Claude can reference real numbers (e.g., "Your BOME win rate is 72% but
your ZEN positions lost $45 on average — consider tighter stops there").
"""

import json
import os
import anthropic

from analytics import get_dashboard_kpis, get_deep_stats
from database  import get_conn
from helpers   import strip_fence
import market_context

ANTHROPIC_API_KEY = os.environ.get(
    "ANTHROPIC_API_KEY",
    ""
)

MODEL = "claude-sonnet-4-6"


def _prune_stats(deep: dict) -> dict:
    """
    Strip empty arrays and low-signal data before feeding to Claude.
    Caps by_symbol to top 10 and by_hour to top 8 most-differentiated hours.
    """
    out = {k: v for k, v in deep.items() if not (isinstance(v, list) and not v)}
    if "by_symbol" in out and isinstance(out["by_symbol"], list):
        out["by_symbol"] = sorted(out["by_symbol"], key=lambda x: -x.get("trade_count", 0))[:10]
    if "by_hour" in out and isinstance(out["by_hour"], list):
        filtered = [h for h in out["by_hour"] if h.get("n", 0) >= 3]
        out["by_hour"] = sorted(filtered, key=lambda x: -abs(x.get("win_rate", 50) - 50))[:8]
    return out


def _build_prompt(kpis: dict, deep: dict, mkt_ctx: str = "") -> str:
    """Serialize the key stats into a structured prompt for Claude."""

    pruned = _prune_stats(deep)

    # Pull only the data that's most useful for analysis (skip raw chart arrays)
    summary = {
        "overview": {
            "total_trades":   kpis["total_trades"],
            "win_rate_pct":   kpis["win_rate"],
            "total_pnl_usdt": kpis["total_pnl"],
            "total_fees_usdt": kpis["total_fees"],
            "best_trade_usdt":  kpis["best_trade"],
            "worst_trade_usdt": kpis["worst_trade"],
            "avg_win_usdt":     kpis["avg_win"],
            "avg_loss_usdt":    kpis["avg_loss"],
            "profit_factor":    kpis["profit_factor"],
            "max_drawdown_usdt": kpis["max_drawdown"],
        },
        "by_symbol":    pruned.get("by_symbol", []),
        "by_month":     pruned.get("by_month", []),
        "by_weekday":   pruned.get("by_weekday", []),
        "by_hour":      pruned.get("by_hour", []),
        "by_direction": pruned.get("by_direction", []),
        "duration_buckets": pruned.get("duration_buckets", []),
        "streaks":      pruned.get("streaks", {}),
        "fee_analysis": pruned.get("fee_analysis", {}),
        "worst_symbols": pruned.get("worst_symbols", []),
    }

    stats_json = json.dumps(summary, indent=2)

    mkt_block = f"\nCURRENT MARKET CONTEXT:\n{mkt_ctx}\n" if mkt_ctx else ""

    return f"""You are a crypto futures trading coach. Analyze this trader's 6-month Bitget USDT-M Futures data and respond with ONLY a valid JSON object — no markdown, no code fences, no explanation outside the JSON.

TRADING STATISTICS:
{stats_json}
{mkt_block}

Respond with this exact JSON structure. Keep each text field concise (2-3 sentences max). Include 3-5 items in strengths, weaknesses, recommendations, and symbol_insights:

{{"overall_status":"2-3 sentence honest summary referencing actual numbers","score":{{"value":1-10,"label":"Poor|Developing|Competent|Good|Excellent"}},"strengths":[{{"title":"short title","detail":"2 sentences with specific numbers"}}],"weaknesses":[{{"title":"short title","detail":"2 sentences with specific numbers"}}],"recommendations":[{{"priority":"High|Medium|Low","title":"short title","action":"one specific action","expected_impact":"one expected result"}}],"symbol_insights":[{{"symbol":"XYZUSDT","insight":"one sentence"}}],"risk_management":"2-3 sentences on position sizing and stops","mindset_note":"one honest encouraging sentence"}}

Reference real numbers (win rates, PnL figures, symbols). No generic advice."""


def analyze(filters: dict = None) -> dict:
    """
    Run the full AI analysis. Returns a dict with Claude's assessment.
    Raises on API error.
    """
    if filters is None:
        filters = {}

    conn = get_conn()
    kpis = get_dashboard_kpis(filters=filters, conn=conn)
    deep = get_deep_stats(filters=filters, conn=conn)
    conn.close()

    ctx     = market_context.get_market_context(["BTCUSDT"])
    mkt_str = market_context.format_for_prompt(ctx)
    prompt  = _build_prompt(kpis, deep, mkt_str)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        messages=[{"role": "user", "content": prompt}]
    )

    # Claude sometimes wraps output in markdown fences even when asked not to
    raw = strip_fence(message.content[0].text.strip())

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "overall_status": raw,
            "score":          {"value": 0, "label": "N/A"},
            "strengths":      [],
            "weaknesses":     [],
            "recommendations": [],
            "symbol_insights": [],
            "risk_management": "",
            "mindset_note":    "",
        }

    result["_model"]        = MODEL
    result["_input_tokens"] = message.usage.input_tokens
    result["_output_tokens"]= message.usage.output_tokens
    return result
