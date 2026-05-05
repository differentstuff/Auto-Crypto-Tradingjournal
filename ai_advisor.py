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

ANTHROPIC_API_KEY = os.environ.get(
    "ANTHROPIC_API_KEY",
    "REDACTED_ANTHROPIC_KEY"
)

MODEL = "claude-sonnet-4-6"


def _build_prompt(kpis: dict, deep: dict) -> str:
    """Serialize the key stats into a structured prompt for Claude."""

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
        "by_symbol":    deep["by_symbol"],
        "by_month":     deep["by_month"],
        "by_weekday":   deep["by_weekday"],
        "by_hour":      deep["by_hour"],
        "by_direction": deep["by_direction"],
        "duration_buckets": deep["duration_buckets"],
        "streaks":      deep["streaks"],
        "fee_analysis": deep["fee_analysis"],
        "worst_symbols": deep["worst_symbols"],
    }

    stats_json = json.dumps(summary, indent=2)

    return f"""You are a crypto futures trading coach. Analyze this trader's 6-month Bitget USDT-M Futures data and respond with ONLY a valid JSON object — no markdown, no code fences, no explanation outside the JSON.

TRADING STATISTICS:
{stats_json}

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

    prompt = _build_prompt(kpis, deep)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Claude sometimes wraps output in markdown code fences (```json ... ```)
    # even when asked not to — strip them before parsing.
    if raw.startswith("```"):
        lines = raw.split("\n")
        # drop first line (```json or ```) and last line (```)
        lines = lines[1:] if lines[-1].strip() == "```" else lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # Parse the JSON response
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Last-resort fallback: show raw text in the overall_status card
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
