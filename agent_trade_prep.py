"""
agent_trade_prep.py — TradePreparation agent.

Owns the main Claude Sonnet call. Assembles prompt from all upstream
agent outputs + stable_prefix for caching. Runs Gemini in parallel.
Generates annotated chart after Claude responds.
"""
import contextvars
import json
from concurrent.futures import ThreadPoolExecutor

from constants import MODEL
from ai_client import send as ai_send
from helpers import strip_fence, build_cached_messages
import prompt_builder
import gemini_client
from consensus import compute_consensus
import agent_chart_draw
import agent_data_interpreter
import agent_risk_mgmt

from agent_types import TradePrepInput, TradePrepResult


def run(inp: TradePrepInput, conn, model: str = MODEL) -> TradePrepResult:
    collected   = inp["collected"]
    interpreted = inp["interpreted"]
    reviewed    = inp["reviewed"]
    sentiment   = inp["sentiment"]
    call_text   = inp["call_text"]
    setup_type  = inp["setup_type"]
    equity      = inp["account_equity"]
    symbol      = collected["symbol"]
    direction   = _infer_direction(call_text) or "Long"

    stable = prompt_builder.build_stable_prefix(conn)

    # Dynamic context assembled from agent outputs
    parts = []
    if reviewed["backtest_context"]:
        parts.append(reviewed["backtest_context"])
    if sentiment["prompt_text"]:
        parts.append(sentiment["prompt_text"])
    if interpreted["prompt_text"]:
        parts.append(interpreted["prompt_text"])
    if reviewed["rubric"]:
        parts.append(f"SETUP RUBRIC ({setup_type}):\n{reviewed['rubric']}")
    sq = reviewed["signal_quality"]
    sq_note = f"SIGNAL QUALITY: {sq:.1f}/10"
    if reviewed["warnings"]:
        sq_note += " — " + "; ".join(reviewed["warnings"])
    parts.append(sq_note)
    dynamic_ctx = "\n\n".join(parts)

    prompt = _build_prompt(call_text, equity, setup_type)

    gemini_result = {}
    # Merge ANALYST + RISK instructions into stable_prefix so they sit inside the
    # cached block (system_prompt alone is ~520 tokens — below Anthropic's 1024-token
    # cache minimum; combined with rulebook/calibration it's ~1540 tokens and caches).
    instructions  = agent_data_interpreter.ANALYST_INSTRUCTIONS + "\n\n" + agent_risk_mgmt.RISK_INSTRUCTIONS
    cached_prefix = instructions + "\n\n" + stable
    # Copy the current context so contextvars (e.g. ai_client.force_provider)
    # propagate into the worker threads — by default ThreadPoolExecutor does not
    # carry context across thread boundaries.
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_claude = ex.submit(
            ctx.run,
            ai_send, "call_analyzer", model,
            build_cached_messages(dynamic_ctx, prompt, stable_prefix=cached_prefix),
            4096,
            None,
        )
        if gemini_client.is_configured() and call_text:
            f_gemini = ex.submit(ctx.run, gemini_client.score_call, call_text, symbol, direction)
        else:
            f_gemini = None

    raw_text, cached = f_claude.result()
    if f_gemini:
        try:
            gemini_result = f_gemini.result() or {}
        except Exception:
            pass

    raw = strip_fence(raw_text.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    claude_score = int(data.get("setup_score", 0))
    gemini_score = int(gemini_result.get("score", 0))
    consensus = (
        compute_consensus(claude_score, gemini_score)
        if gemini_score else {}
    )

    direction_out = data.get("direction", direction)
    entry  = float(data.get("entry_price", 0) or 0)
    sl     = float(data.get("sl_price",    0) or 0)
    tp1    = float(data.get("tp1",         0) or 0)
    tp2    = float(data.get("tp2",         0) or 0)

    # Generate annotated chart
    criteria = []
    if claude_score:
        criteria.append(f"Score {claude_score}/10 · {sentiment.get('macro_bias','?').title()} macro")
    interp_text = interpreted.get("prompt_text", "")
    if interp_text:
        criteria.append(interp_text[:100])
    criteria += (data.get("key_conditions") or [])[:3]
    candles_4h = collected["candles"].get("4H")
    chart_b64 = agent_chart_draw.draw(
        candles=candles_4h,
        symbol=symbol, direction=direction_out,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2,
        criteria=[c for c in criteria if c],
    ) if entry and sl and candles_4h is not None else ""

    return TradePrepResult(
        setup_score      = claude_score,
        direction        = direction_out,
        entry_price      = entry,
        sl_price         = sl,
        tp1_price        = tp1,
        tp2_price        = tp2,
        rr_ratio         = float(data.get("rr_ratio", 0) or 0),
        key_conditions   = data.get("key_conditions", []),
        pattern_warnings = data.get("pattern_warnings", []),
        sizing_hint      = data.get("sizing_hint", ""),
        cot_reasoning    = data.get("cot_reasoning", ""),
        gemini_score     = gemini_score,
        consensus        = consensus,
        raw_json         = data,
        chart_png_b64    = chart_b64,
        _model           = model,
        _cached_tokens   = cached if isinstance(cached, int) else 0,
    )


def _build_prompt(call_text: str, equity: float, setup_type: str) -> str:
    if call_text:
        trade_section = call_text
    else:
        trade_section = (
            "Scanner-generated signal — no analyst call text. "
            "Using technical context above: derive a precise entry_price from the current price "
            "and nearest S/R level, set sl_price at the structural support/resistance below (long) "
            "or above (short) the entry, and set tp1/tp2 at the next liquidity levels. "
            "entry_price MUST be non-zero."
        )
    return f"""You are a professional crypto futures trading analyst. Analyze the trade setup below.

ACCOUNT EQUITY: ${equity:.2f}
SETUP TYPE: {setup_type or "unspecified"}

TRADE CALL:
{trade_section}

Respond with ONLY valid JSON (no markdown, no code fences):
{{"setup_score":1,"direction":"Long","entry_price":0.0,"sl_price":0.0,"tp1":0.0,"tp2":0.0,"rr_ratio":0.0,"key_conditions":["condition 1"],"pattern_warnings":[],"sizing_hint":"one sentence","cot_reasoning":"2-3 sentence chain of thought"}}

Rules:
- setup_score: 1-4=avoid, 5-6=monitor, 7-8=good, 9-10=strong conviction
- Long: sl_price MUST be below entry_price. Short: sl_price MUST be above entry_price.
- entry_price, sl_price, tp1, tp2 MUST all be non-zero real prices
- tp1 = conservative target (1.5:1 R:R min), tp2 = full target (2.5:1+ R:R)
- Reference the context provided above (backtest WR, sentiment, indicators, S/R levels)
- cot_reasoning: state the 2-3 strongest reasons for your score"""


def _infer_direction(text: str) -> str:
    lower = (text or "").lower()
    if any(w in lower for w in ("short", "sell", "bear")):
        return "Short"
    return "Long"
