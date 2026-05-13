#!/usr/bin/env python3
"""
scripts/generate_architecture_pdf.py
Generates a detailed architecture PDF for the Trading Journal AI Agent system.
Run from the project root: python3 scripts/generate_architecture_pdf.py
"""

import os
import sys
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus.flowables import Flowable

# ── Colour palette ──────────────────────────────────────────────────────────
C_BG        = colors.HexColor("#0d1117")
C_SURFACE   = colors.HexColor("#161b22")
C_BORDER    = colors.HexColor("#30363d")
C_ACCENT    = colors.HexColor("#6c63ff")   # purple
C_ACCENT2   = colors.HexColor("#4fc3f7")   # teal
C_ACCENT3   = colors.HexColor("#26d96b")   # green
C_YELLOW    = colors.HexColor("#ffb300")
C_RED       = colors.HexColor("#ef5350")
C_TEXT      = colors.HexColor("#e6edf3")
C_MUTED     = colors.HexColor("#8b949e")
C_SONNET    = colors.HexColor("#4a90d9")
C_HAIKU     = colors.HexColor("#7ed3a6")
C_GEMINI    = colors.HexColor("#4285F4")
C_GROK      = colors.HexColor("#1d9bf0")
C_NANSEN    = colors.HexColor("#f7931a")

OUTPUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "docs", "architecture_detailed.pdf")


# ── Horizontal rule flowable ────────────────────────────────────────────────
class ColoredRule(Flowable):
    def __init__(self, color, width_pct=1.0, thickness=0.5):
        super().__init__()
        self.color = color
        self.width_pct = width_pct
        self.thickness = thickness
        self.height = thickness + 2

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        w = self.canv._pagesize[0] * self.width_pct
        self.canv.line(0, 0, w, 0)


# ── Styles ───────────────────────────────────────────────────────────────────
def make_styles():
    base = getSampleStyleSheet()
    def P(name, parent="Normal", **kwargs):
        return ParagraphStyle(name, parent=base[parent], **kwargs)

    return {
        "cover_title": P("cover_title",
            fontSize=32, leading=40, textColor=C_TEXT,
            fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=8),
        "cover_sub": P("cover_sub",
            fontSize=14, leading=20, textColor=C_ACCENT2,
            fontName="Helvetica", alignment=TA_CENTER, spaceAfter=4),
        "cover_meta": P("cover_meta",
            fontSize=10, leading=14, textColor=C_MUTED,
            fontName="Helvetica", alignment=TA_CENTER),

        "h1": P("h1",
            fontSize=22, leading=28, textColor=C_ACCENT,
            fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=6),
        "h2": P("h2",
            fontSize=16, leading=22, textColor=C_ACCENT2,
            fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4),
        "h3": P("h3",
            fontSize=13, leading=18, textColor=C_YELLOW,
            fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=3),

        "body": P("body",
            fontSize=10, leading=15, textColor=C_TEXT,
            fontName="Helvetica", spaceAfter=6, alignment=TA_JUSTIFY),
        "body_b": P("body_b",
            fontSize=10, leading=15, textColor=C_TEXT,
            fontName="Helvetica-Bold", spaceAfter=4),
        "small": P("small",
            fontSize=8.5, leading=13, textColor=C_MUTED,
            fontName="Helvetica", spaceAfter=3),
        "code": P("code",
            fontSize=8.5, leading=13, textColor=C_ACCENT3,
            fontName="Courier", spaceAfter=4,
            backColor=colors.HexColor("#0d1117"),
            borderPadding=(4, 6, 4, 6)),
        "bullet": P("bullet",
            fontSize=10, leading=15, textColor=C_TEXT,
            fontName="Helvetica", leftIndent=14,
            bulletIndent=4, spaceAfter=3),
        "tag": P("tag",
            fontSize=8, leading=11, textColor=C_BG,
            fontName="Helvetica-Bold", alignment=TA_CENTER),
    }


# ── Table helpers ─────────────────────────────────────────────────────────────
def make_table(data, col_widths, header_bg=C_ACCENT, row_colors=True, font_size=9):
    style = [
        ("BACKGROUND",  (0,0), (-1,0),  header_bg),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_TEXT),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), font_size),
        ("ROWBACKGROUNDS", (0,1), (-1,-1),
            [colors.HexColor("#161b22"), colors.HexColor("#1a2030")]
            if row_colors else [C_SURFACE]),
        ("TEXTCOLOR",   (0,1), (-1,-1), C_TEXT),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("GRID",        (0,0), (-1,-1), 0.25, C_BORDER),
        ("ALIGN",       (0,0), (-1,-1), "LEFT"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 7),
        ("RIGHTPADDING",(0,0), (-1,-1), 7),
        ("LINEBELOW",   (0,0), (-1,0),  1.0, C_ACCENT),
    ]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle(style))
    return t


def agent_card(name, model_color, model_label, trigger, output, desc_short, desc_long, styles):
    label_style = TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), model_color),
        ("TEXTCOLOR",   (0,0), (-1,-1), C_BG),
        ("FONTNAME",    (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 7),
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0),(-1,-1), 2),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING",(0,0), (-1,-1), 5),
        ("BOX",         (0,0), (-1,-1), 0.25, C_BORDER),
    ])
    tag = Table([[model_label]], colWidths=[2.5*cm])
    tag.setStyle(label_style)

    header_data = [[
        Paragraph(f"<b>{name}</b>", ParagraphStyle("ch", fontSize=11, fontName="Helvetica-Bold",
                                                    textColor=C_TEXT, leading=14)),
        tag,
    ]]
    header_t = Table(header_data, colWidths=[12*cm, 2.8*cm])
    header_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), colors.HexColor("#1a2030")),
        ("ALIGN",       (0,0), (0,0),   "LEFT"),
        ("ALIGN",       (1,0), (1,0),   "RIGHT"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
        ("LEFTPADDING", (0,0), (0,0),   10),
        ("RIGHTPADDING",(1,0), (1,0),   10),
        ("LINEBELOW",   (0,0), (-1,0),  0.5, model_color),
    ]))

    body_data = [[
        Paragraph(f"<b>Trigger:</b> {trigger}", ParagraphStyle("ct", fontSize=9, fontName="Helvetica",
                                                                 textColor=C_MUTED, leading=13)),
        Paragraph(f"<b>Output:</b> {output}", ParagraphStyle("co", fontSize=9, fontName="Helvetica",
                                                               textColor=C_MUTED, leading=13)),
    ]]
    meta_t = Table(body_data, colWidths=[7.4*cm, 7.4*cm])
    meta_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), C_SURFACE),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING", (0,0), (0,0),   10),
        ("RIGHTPADDING",(1,0), (1,0),   10),
        ("GRID",        (0,0), (-1,-1), 0.25, C_BORDER),
    ]))

    desc_data = [[
        Paragraph(f"<i>{desc_short}</i>", ParagraphStyle("ds", fontSize=9, fontName="Helvetica-Oblique",
                                                           textColor=C_ACCENT2, leading=13)),
    ]]
    desc_t = Table(desc_data, colWidths=[14.8*cm])
    desc_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), C_SURFACE),
        ("TOPPADDING",  (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING",(0,0), (-1,-1), 10),
    ]))

    long_data = [[
        Paragraph(desc_long, ParagraphStyle("dl", fontSize=9, fontName="Helvetica",
                                             textColor=C_TEXT, leading=14, alignment=TA_JUSTIFY)),
    ]]
    long_t = Table(long_data, colWidths=[14.8*cm])
    long_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), C_SURFACE),
        ("TOPPADDING",  (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING",(0,0), (-1,-1), 10),
        ("LINEBELOW",   (0,0), (-1,-1), 0.5, C_BORDER),
        ("LINEBEFORE",  (0,0), (0,-1),  2.0, model_color),
    ]))

    return KeepTogether([header_t, meta_t, desc_t, long_t, Spacer(1, 6)])


# ── Content builder ───────────────────────────────────────────────────────────
def build_story(styles):
    S = styles
    story = []

    # ── COVER PAGE ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("Trading Journal", S["cover_title"]))
    story.append(Paragraph("AI Agent Architecture", S["cover_sub"]))
    story.append(Spacer(1, 0.4*cm))
    story.append(ColoredRule(C_ACCENT, 0.6))
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph("Multi-Model Intelligence Framework — v1.0.1", S["cover_meta"]))
    story.append(Paragraph("Claude Sonnet · Claude Haiku · Google Gemini · xAI Grok · Nansen · CoinGecko", S["cover_meta"]))
    story.append(Spacer(1, 1*cm))

    intro = make_table([
        ["What this document covers"],
        ["This document describes every AI agent, data agent, and automation agent that\n"
         "powers the self-hosted crypto futures trading journal. Each section explains\n"
         "what the agent does, why it was designed that way, what model it uses, when\n"
         "it fires, and how it connects to the others. Suitable for beginners and experts."],
    ], [15*cm], header_bg=C_ACCENT)
    story.append(intro)
    story.append(PageBreak())

    # ── SECTION 1: OVERVIEW ──────────────────────────────────────────────────
    story.append(Paragraph("1. System Overview", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "The trading journal is a self-hosted application running on a Raspberry Pi 5. "
        "It connects to cryptocurrency futures exchanges (Bitget, Blofin), tracks your "
        "trade history, and uses a network of specialised AI agents to help you make "
        "better trading decisions. Every agent has a specific, focused responsibility — "
        "none of them tries to do everything.", S["body"]))

    story.append(Paragraph(
        "For <b>beginners:</b> think of it as a team of analysts, each an expert in one area. "
        "One analyst reads the charts, another checks social media, a third scores your "
        "trade ideas, another reviews your history. The orchestrator is the team leader "
        "who decides who speaks and how much weight to give each opinion.", S["body"]))

    story.append(Paragraph(
        "For <b>experts:</b> the architecture uses a prompt-caching-aware stable/dynamic "
        "context split, a consensus scoring layer across two independent LLMs with "
        "divergence detection, MC-weighted external intelligence (Grok), and a backtest "
        "feedback loop that injects historical win-rate patterns directly into every "
        "scoring prompt.", S["body"]))

    story.append(Spacer(1, 6))
    story.append(Paragraph("Three types of agents", S["h2"]))

    overview_data = [
        ["Type", "What it means", "Examples"],
        ["Analysis Agents",
         "Receive input (a trade call, a position, historical data) and return a "
         "structured AI-powered assessment. Called on-demand by the user.",
         "call_analyzer, advisor, hindsight, live_trade"],
        ["Automation Agents",
         "Run on a schedule in the background without any user action. They fetch "
         "data, detect setups, send alerts, sync positions from exchanges.",
         "scanner_scheduler, bitget_sync, blofin_sync"],
        ["Data Agents",
         "Fetch and cache external data (no AI). They are called by analysis agents "
         "to enrich prompts with market context, on-chain signals, or social intel.",
         "nansen_client, grok_client, gemini_client, market_context, chart_context"],
    ]
    story.append(make_table(overview_data,
        [3.5*cm, 7*cm, 4.3*cm], header_bg=C_ACCENT))
    story.append(PageBreak())

    # ── SECTION 2: MASTER ORCHESTRATOR ───────────────────────────────────────
    story.append(Paragraph("2. Master Orchestrator", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "The orchestrator (<b>agent_orchestrator.py</b>) does not call any AI model itself. "
        "Its job is to coordinate results from multiple models and make routing decisions. "
        "It answers two questions: <i>which model should handle this task?</i> and "
        "<i>do Claude and Gemini agree on this trade signal?</i>", S["body"]))

    story.append(Paragraph("2.1 Model Router", S["h2"]))
    story.append(Paragraph(
        "Every AI task in the journal is classified as either a <b>reasoning task</b> "
        "(needs the most capable model) or a <b>classification task</b> (can be done by "
        "a faster, cheaper model). This matters because Claude Sonnet costs roughly "
        "10× more per token than Claude Haiku — routing correctly reduces costs without "
        "sacrificing accuracy.", S["body"]))

    router_data = [
        ["Task", "Model", "Reason for choice"],
        ["call_analyzer",    "Sonnet 4.6", "Complex structured JSON output with 15+ fields, chain-of-thought scoring"],
        ["scanner_batch",    "Sonnet 4.6", "Evaluates 12 symbols simultaneously, needs nuanced entry/SL/TP reasoning"],
        ["advisor",          "Sonnet 4.6", "Full portfolio coaching from 800+ trades, long-form recommendations"],
        ["rulebook",         "Sonnet 4.6", "Synthesises entire trade history into personalised trading rules"],
        ["limit_analyzer",   "Sonnet 4.6", "Risk decision on a pending order — accuracy critical"],
        ["pattern_detector", "Sonnet 4.6", "Cross-pattern compounding analysis — needs genuine reasoning"],
        ["scanner_quick",    "Haiku 4.5",  "Score 0-10 + one sentence — pure classification, runs 100× per scan"],
        ["live_trade",       "Haiku 4.5",  "Hold/Close/Adjust action — simple rubric, latency matters"],
        ["hindsight",        "Haiku 4.5",  "Retroactive ENTER/SKIP verdict — binary classification task"],
        ["trade_grader",     "Haiku 4.5",  "A/B/C/D execution grade — simple rubric, runs once per closed trade"],
    ]
    story.append(make_table(router_data,
        [3.5*cm, 3*cm, 8.3*cm], header_bg=C_ACCENT, font_size=8.5))

    story.append(Spacer(1, 10))
    story.append(Paragraph("2.2 Consensus Scoring Algorithm", S["h2"]))
    story.append(Paragraph(
        "When a user analyzes a trade call, both Claude and Google Gemini score the "
        "setup independently. Claude receives the full context (rulebook, chart data, "
        "market conditions, similar historical trades). Gemini receives <i>only the raw "
        "call text</i> — no extra context. This is intentional: two assessors with "
        "different information sets produce more meaningful agreement or disagreement "
        "than two copies of the same prompt.", S["body"]))

    story.append(Paragraph(
        "When they agree (|Δ| ≤ 1), confidence is high — the setup signal is robust. "
        "When they strongly disagree (|Δ| > 3), the trade is flagged for manual review "
        "before acting on it. The weighted average (Claude 60%, Gemini 40% on mild "
        "disagreements) reflects that Claude's full context generally produces more "
        "accurate scores for structured technical setups.", S["body"]))

    consensus_data = [
        ["Delta (|Claude − Gemini|)", "Confidence", "Score used", "UI flag", "Recommended action"],
        ["0 – 1 point",    "High",      "Simple average",        "✓ Confirmed", "Trade with normal risk"],
        ["2 points",       "Medium",    "Simple average",        "~ Aligned",   "Trade, monitor closely"],
        ["3 points",       "Low",       "Claude 60% + Gemini 40%","⚠ Divergent", "Reduce size or wait for confirmation"],
        ["> 3 points",     "Very Low",  "Claude score kept",     "⚡ REVIEW",   "Do not trade — investigate the disagreement"],
    ]
    story.append(make_table(consensus_data,
        [3.5*cm, 2.2*cm, 3.2*cm, 2.5*cm, 3.4*cm],
        header_bg=colors.HexColor("#4a2080"), font_size=8.5))
    story.append(PageBreak())

    # ── SECTION 3: ANALYSIS AGENTS ───────────────────────────────────────────
    story.append(Paragraph("3. Analysis Agents", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "Analysis agents are triggered by user actions (clicking a button, "
        "requesting an analysis). Each one receives a focused input, builds an "
        "optimised prompt from the shared context system, calls the AI, and returns "
        "structured JSON.", S["body"]))

    agents_data = [
        # (name, model_color, model_label, trigger, output, short, long)
        (
            "📊 Call Analyzer  —  ai_call.py",
            C_SONNET, "Sonnet 4.6",
            "User pastes analyst call text (Telegram, Twitter, manual)",
            "Score 1-10, entry/SL/TP, sizing, pattern warnings, R:R, Gemini consensus",
            "The primary intelligence agent for evaluating trade calls from external analysts.",
            "When a user receives a trade call from an analyst on Telegram, they paste "
            "it here. The agent automatically extracts the symbol, direction, entry price, "
            "stop loss and take profit levels using regex. It then runs three things in "
            "parallel: (1) an ATR-based stop loss quality check using 1H candle data, "
            "(2) live market context (funding rates, Fear & Greed), and (3) Gemini's "
            "independent pre-proof score. Claude then receives the full context — "
            "personalised rulebook, calibration feedback, chart indicators for 4H and 1D, "
            "Nansen smart money signal, Grok social sentiment (weighted by market cap), "
            "similar historical trades — and produces a 15-field structured analysis. "
            "The previous analysis for the same symbol is injected as a learning loop "
            "(CoT reuse), enabling Claude to detect if setup conditions have changed. "
            "The consensus score (Claude vs Gemini) is saved to the database alongside "
            "the full analysis."
        ),
        (
            "📈 Scanner  —  ai_scanner.py (3-stage)",
            C_SONNET, "Sonnet + Haiku",
            "Every 30 minutes (automatic) or manual run",
            "Up to 12 scored setups with entry_zone, sl, tp1, tp2, rr_ratio, urgency",
            "Proactively finds trade setups across 100 symbols without waiting for analyst calls.",
            "Stage 1 (Confluence Filter, no AI): fetches 4H and 1D candles for all 100 "
            "symbols in parallel and computes RSI, MACD, EMA, ADX, WaveTrend, and CVD. "
            "Symbols where fewer than 2 indicators agree on direction are eliminated. "
            "This typically cuts 100 to ~25-30 candidates with zero API cost. "
            "Stage 2 (Quality Gate, no AI): applies technical rules — rejects overextended "
            "RSI, missing S/R structure, flat ADX (choppy market), very high funding rate. "
            "This cuts to ~10-15 finalists. "
            "Stage 3a (Haiku Quick Score): Haiku scores each finalist with a minimal "
            "prompt (120 tokens output max) — faster and 10× cheaper than Sonnet. Setups "
            "scoring below threshold are dropped. "
            "Stage 3b (Sonnet Batch): all remaining finalists are scored in a SINGLE "
            "Sonnet call using a batch prompt. This is a key token optimisation — scoring "
            "12 symbols simultaneously rather than 12 sequential calls. "
            "Stage 3c (Gemini Consensus): top-5 finalists receive an independent Gemini "
            "score. The final ranking adjusts based on consensus confidence. "
            "Alerted setups are automatically saved to analyzed_calls so they can be "
            "linked to live positions without manual intervention."
        ),
        (
            "🧠 AI Advisor  —  ai_advisor.py",
            C_SONNET, "Sonnet 4.6",
            "User clicks 'Get AI Advice' in the Edge Lab",
            "Portfolio strengths, weaknesses, specific recommendations, symbol insights",
            "High-level portfolio coaching based on full trade history and current market.",
            "The advisor receives aggregated statistics from your entire trade history: "
            "win rate, profit factor, performance by symbol, by weekday, by hour, by "
            "setup type, by duration. It also receives the current market context "
            "(BTC dominance, Fear & Greed, funding rates). The rulebook and calibration "
            "data are cached as the stable prefix. Claude identifies patterns across "
            "all dimensions and produces a structured coaching report: what you're doing "
            "well (strengths), where you're losing money (weaknesses), and 3-5 specific "
            "actionable recommendations with data to back them up. For example: "
            "'Your Friday afternoon LONG trades have a 43% win rate vs 71% on other days "
            "— consider avoiding new positions after 15:00 UTC on Fridays.'"
        ),
        (
            "🔮 Hindsight Analyzer  —  ai_hindsight.py",
            C_HAIKU, "Haiku 4.5",
            "User runs hindsight batch (last N trades)",
            "ENTER/SKIP verdict per trade, TP/FP/TN/FN accuracy, comparison P&L",
            "Retroactive blind scoring: what would AI have said if it saw the setup before the outcome?",
            "This is a backtesting tool with a key discipline: Claude is shown the "
            "technical picture as it appeared AT ENTRY TIME — not the current chart. "
            "The agent fetches historical OHLCV candles ending at the exact trade entry "
            "timestamp and reconstructs the indicator state. It then scores the setup "
            "without knowing how the trade ended. "
            "The results show: (1) how often Claude's ENTER calls actually won (True "
            "Positive rate), (2) how often its SKIP calls would have been correct, and "
            "(3) the hypothetical P&L if you had only taken trades Claude would rate ≥6. "
            "This calibration loop helps identify whether the AI's scoring is predictive "
            "of actual outcomes — the foundation for the 85% accuracy target."
        ),
        (
            "👁 Live Trade Checker  —  ai_live_trade.py",
            C_HAIKU, "Haiku 4.5",
            "User clicks '🤖 AI Analysis' on a live position card",
            "Action (Hold/Close/Adjust SL), risk rating 1-10, TP/SL suggestions",
            "Per-position quick health check for open futures positions.",
            "Receives the full position data (entry, mark, SL, TP, duration, margin, "
            "unrealized P&L, funding rate) plus the current 4H chart indicators. Haiku "
            "evaluates whether the trade thesis is still valid, whether the stop is at "
            "risk, and whether the position has been open too long. Returns a structured "
            "recommendation in under 2 seconds. Haiku is used here specifically because "
            "the user is looking at their live portfolio and latency matters — a 5-second "
            "wait for each card would be frustrating."
        ),
        (
            "⏳ Limit Analyzer  —  ai_limit.py",
            C_SONNET, "Sonnet 4.6",
            "User clicks 'Analyze' on a pending limit order",
            "Entry quality score, risk assessment, ATR validation",
            "Evaluates limit orders before they trigger — catching bad setups before they fill.",
            "Limit orders represent planned trades that haven't executed yet. The limit "
            "analyzer checks whether the planned entry price is at a structurally sound "
            "level, whether the stop loss is outside the ATR noise floor, and how this "
            "limit fits with other open or pending positions. Uses Sonnet because this is "
            "a consequential decision (real money at risk when the limit fills) and the "
            "nuanced entry quality assessment benefits from the more capable model."
        ),
        (
            "🏆 Trade Grader  —  ai_trade_grader.py",
            C_HAIKU, "Haiku 4.5",
            "User clicks '⚡ Grade' on any closed trade",
            "Grade A/B/C/D with written explanation of execution quality",
            "Execution quality feedback loop — was the entry/exit actually well-executed?",
            "Separate from whether the trade was profitable, the grader evaluates "
            "<i>how well</i> the trade was executed: did the entry happen near the ideal "
            "zone, was the stop set correctly relative to structure, was the exit too "
            "early or too late, was risk sizing appropriate? A trade can be a B+ execution "
            "that still lost money (correct process, bad luck) or a D execution that won "
            "by chance. Tracking execution grades over time identifies whether losses "
            "come from bad setups or bad execution — very different problems requiring "
            "different solutions."
        ),
        (
            "📖 Rulebook Generator  —  ai_rulebook.py",
            C_SONNET, "Sonnet 4.6",
            "Weekly auto-regen (if 5+ new trades) or manual update",
            "10 personalised rules with confidence levels and stale annotations",
            "Self-updating trading rulebook synthesised from your actual trade history.",
            "The rulebook is the most impactful agent for long-term accuracy improvement. "
            "Claude reads your complete trade statistics — performance by setup type, "
            "symbol, session, weekday, hour, holding period, and direction — and "
            "synthesises 5-10 personalised rules. These are not generic advice but rules "
            "derived from YOUR specific data. For example: 'Friday morning breakouts: "
            "3 trades, 0 wins, avg -$166 — strong evidence to avoid.' "
            "Rules older than 30 days are annotated [stale] so Claude discounts them in "
            "analysis prompts. A regen guard prevents regeneration if fewer than 5 new "
            "trades exist since the last update — avoiding rules based on too little data. "
            "All 10 rules are injected into every analysis prompt as the cached stable "
            "prefix, meaning Claude always has your personalised context without paying "
            "for it on every call."
        ),
    ]

    for agent in agents_data:
        story.append(agent_card(*agent, styles))

    story.append(PageBreak())

    # ── SECTION 4: EXTERNAL AI PROVIDERS ─────────────────────────────────────
    story.append(Paragraph("4. External AI Providers", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "Two external AI providers are integrated alongside Claude to provide "
        "independent signals that cannot come from technical analysis alone.", S["body"]))

    story.append(Paragraph("4.1  Google Gemini — Independent Pre-Proof Scorer", S["h2"]))
    story.append(Paragraph(
        "<b>Why Gemini?</b> Having a second AI model score the same trade call "
        "independently creates a cross-validation signal. Gemini sees only the raw "
        "call text — no rulebook, no chart context. This information asymmetry is "
        "deliberate. If two models with different training data and different context "
        "reach the same score, the signal is stronger. If they strongly disagree, "
        "that disagreement is itself informative.", S["body"]))

    story.append(Paragraph(
        "<b>For beginners:</b> Imagine having two doctors give independent opinions "
        "on the same X-ray without telling either what the other said. If both say "
        "'this looks fine,' you're confident. If one says 'this is serious' and the "
        "other says 'nothing to worry about,' you know to get a third opinion.", S["body"]))

    story.append(Paragraph(
        "<b>Technical details:</b> Uses Gemini 2.0 Flash (fast, cheap) via the Google "
        "Generative Language API with <code>responseMimeType: application/json</code> "
        "to force structured output. Runs in parallel with ATR checks and market context "
        "fetches — no additional wall-clock time. Cached 30 minutes per (symbol, "
        "direction) pair. Results stored in <code>analyzed_calls.gemini_score</code> and "
        "<code>consensus_score</code> columns for backtesting.", S["body"]))

    story.append(Spacer(1, 8))
    story.append(Paragraph("4.2  xAI Grok — Social Intelligence (X/Twitter)", S["h2"]))
    story.append(Paragraph(
        "<b>Why Grok?</b> Grok has real-time access to X (Twitter) data. For "
        "small-cap and micro-cap crypto assets, the price is often driven more by "
        "social narrative and community sentiment than by on-chain fundamentals or "
        "technical patterns. Grok is the only AI in the stack that can see what "
        "people are saying about a specific coin right now.", S["body"]))

    story.append(Paragraph(
        "<b>Market cap weighting:</b> For large-cap coins like Bitcoin or Ethereum, "
        "social media noise far exceeds the signal — institutional trading dominates, "
        "and a viral tweet rarely moves the price meaningfully. For micro-cap coins "
        "($200M market cap or below), a single influential post can move price 30%. "
        "The weight formula reflects this reality:", S["body"]))

    grok_weight_data = [
        ["Market Cap", "Grok Weight", "Rationale"],
        ["> $5 billion",        "0%  — skipped",  "Large cap: social noise > signal. Institutional flows dominate."],
        ["$1B – $5B",           "15% weight",      "Mid-large: supplementary context only."],
        ["$200M – $1B",         "40% weight",      "Small cap: social sentiment is a meaningful price driver."],
        ["< $200M",             "80% weight",      "Micro cap: social narrative is often the PRIMARY driver."],
        ["Unknown market cap",  "60% weight",      "Treated as small-cap until CoinGecko confirms otherwise."],
    ]
    story.append(make_table(grok_weight_data,
        [3.5*cm, 2.5*cm, 8.8*cm], header_bg=C_GROK, font_size=8.5))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<b>What Grok provides:</b> a 100-130 word brief covering X/Twitter sentiment "
        "(bullish/bearish/mixed and dominant narratives), recent news or developments "
        "(last 7 days), social quality assessment (organic analysis vs. coordinated "
        "hype), and the biggest social/news risk to the current trade direction. Red "
        "flags are explicitly marked with ⚠. The brief is injected into the prompt "
        "context with a label showing the weight so Claude knows how much to rely on "
        "it relative to technical indicators.", S["body"]))
    story.append(PageBreak())

    # ── SECTION 5: DATA AGENTS ────────────────────────────────────────────────
    story.append(Paragraph("5. Data Agents", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "Data agents fetch, cache, and format external data. They don't call any AI "
        "model — they are pure data pipelines whose output enriches AI prompts.", S["body"]))

    data_agents = [
        ["Agent", "Source", "Cache", "What it provides", "Injected into"],
        ["chart_context.py",  "Bitget REST v2", "10 min",
         "OHLCV candles → RSI, MACD, EMA(20/50/200), ADX, ATR, Bollinger, StochRSI, WaveTrend Cipher A/B, CVD, S/R levels, trendlines, Fibonacci",
         "All analysis agents"],
        ["market_context.py", "Bitget + Bybit + Binance + OKX + alternative.me + FRED", "5 min",
         "Funding rates (4 exchanges avg), Fear & Greed 0-100, Long/Short ratio, Open Interest, BTC regime (bull/bear/range), macro: Fed rate/treasury/CPI/M2",
         "call_analyzer, scanner, advisor"],
        ["nansen_client.py",  "Nansen.ai screener",  "30 min",
         "On-chain wallet activity: netflow direction (accumulating/distributing), trader count, strength (weak/moderate/strong). Minimum 5 wallets for signal.",
         "call_analyzer, scanner"],
        ["grok_client.py",    "xAI Responses API",   "30 min",
         "X/Twitter sentiment, recent news, social quality, red flags. MC-weighted.",
         "call_analyzer, scanner (via prompt_builder)"],
        ["gemini_client.py",  "Google Generative Language API", "30 min",
         "Independent pre-proof score 1-10 with concerns list. Lean prompt (no rulebook/chart).",
         "call_analyzer (parallel), scanner (top-5 consensus)"],
    ]
    story.append(make_table(data_agents,
        [3.2*cm, 3.2*cm, 1.6*cm, 4.5*cm, 3.3*cm],
        header_bg=C_ACCENT2, font_size=8))

    story.append(Spacer(1, 10))
    story.append(Paragraph("5.1  Chart Context Architecture", S["h2"]))
    story.append(Paragraph(
        "The chart pipeline is split into three pure modules for testability and "
        "maintainability:", S["body"]))

    chart_data = [
        ["Module", "Responsibility", "Key functions"],
        ["chart_indicators.py", "Pure indicator computation — no API calls, no side effects",
         "compute_rsi, compute_macd, compute_ema_alignment, compute_adx, compute_wavetrend (VMC Cipher A/B), compute_cvd, compute_all_indicators"],
        ["chart_sr.py", "S/R detection with ATR-relative tolerance and recency weighting",
         "detect_support_resistance (ATR-relative clustering, exponential decay on touch recency), nearest_levels"],
        ["chart_context.py", "Thin orchestrator: fetches candles, caches, calls the two above",
         "get_candles (Bitget, 10-min cache), compute_indicators (delegates to above), confluence_score, get_candles_for_chart, detect_trendlines, detect_fibonacci"],
    ]
    story.append(make_table(chart_data,
        [3.5*cm, 5.5*cm, 5.8*cm], header_bg=C_ACCENT2, font_size=8.5))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>VMC Cipher A/B (WaveTrend Oscillator):</b> implemented from scratch matching "
        "TradingView's VuManChu Cipher B defaults (n1=10, n2=21). Provides gold_buy, "
        "buy, and sell signals based on oversold/overbought crossovers. Adds a 7th "
        "confluence signal to the scoring system. Rendered as a separate oscillator "
        "pane below the candlestick chart in the browser.", S["body"]))
    story.append(PageBreak())

    # ── SECTION 6: AUTOMATION AGENTS ─────────────────────────────────────────
    story.append(Paragraph("6. Automation Agents", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "Automation agents run continuously in background threads. They require no "
        "user interaction — they watch the market, sync positions, and fire alerts "
        "automatically.", S["body"]))

    story.append(Paragraph("6.1  Scanner Scheduler  —  scanner_scheduler.py", S["h2"]))
    story.append(Paragraph(
        "Runs the full 3-stage scanner pipeline every 30 minutes. First scan fires "
        "5 minutes after app startup (allowing exchange sync to complete). "
        "After every run that produces results above the score threshold:", S["body"]))

    sched_steps = [
        "1. Sends a Telegram HTML alert with symbol, direction, score, entry zone, SL, TP1, TP2, R:R, and urgency.",
        "2. Saves each alerted setup to <code>analyzed_calls</code> with analyst='scanner'. This is critical for automatic "
        "position linking — when a scanner-alerted position opens on the exchange, <code>check-matches</code> "
        "auto-confirms the link without user action.",
        "3. Deduplicates by (symbol, direction) within a 4-hour window to prevent spam on consecutive scans.",
    ]
    for s in sched_steps:
        story.append(Paragraph(s, S["bullet"]))

    story.append(Spacer(1, 6))
    story.append(Paragraph("6.2  Exchange Sync  —  bitget_sync.py / blofin_sync.py", S["h2"]))
    story.append(Paragraph(
        "Runs every 5 minutes in a background thread. Uses cursor-based pagination to "
        "catch all closed positions regardless of holding duration. Key behaviours:", S["body"]))

    sync_features = [
        ["Feature", "What it does"],
        ["Auto-close calls",    "When a position closes, finds the linked analyzed_call and marks it closed. "
                                "Records which TP or SL was hit based on close price vs levels."],
        ["Market regime tagging","Tags each position bull/bear/range at entry time using BTC EMA50/200 cross "
                                 "(get_btc_regime). Enables filtering analytics by market condition."],
        ["MFE/MAE tracking",    "Records Maximum Favourable Excursion and Maximum Adverse Excursion for "
                                "each trade. Used for the 'did you exit too early?' analytics."],
        ["Deduplication",       "Idempotent — safe to run every 5 minutes. Uses exchange order IDs to "
                                "prevent duplicate entries regardless of how many times sync fires."],
        ["Catch-up window",     "On startup, fetches trades from the last 48 hours to recover any "
                                "missed during downtime (Pi restart, network outage, etc.)"],
    ]
    story.append(make_table(sync_features,
        [3.8*cm, 11*cm], header_bg=colors.HexColor("#2a5090"), font_size=8.5))
    story.append(PageBreak())

    # ── SECTION 7: PROMPT ARCHITECTURE ───────────────────────────────────────
    story.append(Paragraph("7. Prompt Architecture & Caching", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "How prompts are built and cached is one of the most important architectural "
        "decisions in the system — it directly affects both cost and accuracy.", S["body"]))

    story.append(Paragraph("7.1  The Stable / Dynamic Split", S["h2"]))
    story.append(Paragraph(
        "Anthropic's prompt caching works by storing a prefix that is byte-for-byte "
        "identical across calls. When the cached prefix is reused, Anthropic charges "
        "approximately 10% of the normal input token price for the cached portion. "
        "However, if any live data (funding rates, chart indicators, market context) "
        "is included in the cached block, the cache key changes every few minutes and "
        "cache hits never occur — you pay full price on every call.", S["body"]))

    story.append(Paragraph(
        "The fix: <b>build_stable_prefix()</b> returns only content that changes at "
        "most weekly (rulebook + calibration + pattern strengths). "
        "<b>build_context()</b> returns dynamic content that changes per call "
        "(backtest insights, market data, chart indicators, Nansen, Grok, similar "
        "trades). The stable prefix gets <code>cache_control: ephemeral</code> — "
        "the dynamic context does not.", S["body"]))

    cache_data = [
        ["Block", "Content", "Cache?", "Changes how often"],
        ["Stable prefix (Block 1)", "Rulebook (10 rules), calibration feedback, top-3 pattern strengths",
         "✓ YES\ncache_control:\nephemeral", "Weekly (when 5+ new trades)"],
        ["Dynamic context (Block 2)", "Backtest insights, live market context, chart indicators, Nansen signal, "
         "Grok social brief, similar historical trades",
         "✗ NO", "Every call (market data every 5 min)"],
        ["Variable prompt (Block 3)", "Call text, position sizing, CoT from previous same-symbol analysis",
         "✗ NO", "Every call"],
    ]
    story.append(make_table(cache_data,
        [3.5*cm, 6.5*cm, 2.0*cm, 2.8*cm], header_bg=C_ACCENT, font_size=8.5))

    story.append(Spacer(1, 8))
    story.append(Paragraph("7.2  Backtest Feedback Loop", S["h2"]))
    story.append(Paragraph(
        "Every Claude analysis prompt begins with a compact historical performance "
        "block injected by <b>get_backtest_context()</b> in analytics.py. This "
        "gives Claude specific, numerical context about YOUR trading patterns before "
        "it scores any new call:", S["body"]))

    story.append(Paragraph(
        "<i>Example backtest context injected into a prompt:</i>", S["small"]))

    example_bt = (
        "BACKTEST INSIGHTS:\n"
        "  Recent form: 72% WR last 20 · streak WWLWW · avg +$8.40\n"
        "  Breakout setups: 100% WR (6 trades) avg +$7.00\n"
        "  BTCUSDT Long: 75% WR (12 trades) avg +$12.50\n"
        "  ⚠ Wednesday: caution (57% WR, -$355 total P&L)\n"
        "  ⚠ 21:00 UTC: weak hour (70% WR, -$1831 total)"
    )
    story.append(Paragraph(example_bt, S["code"]))

    story.append(Paragraph(
        "This is not generic advice — it is derived from the user's actual trade "
        "history in real time. Claude sees both the opportunity signal (technical setup) "
        "and the historical context (does this trader actually profit from this type "
        "of setup at this time of day?) before assigning a score. This is the primary "
        "mechanism for improving accuracy as trade history grows.", S["body"]))

    story.append(Spacer(1, 8))
    story.append(Paragraph("7.3  CoT Learning Loop", S["h2"]))
    story.append(Paragraph(
        "When Claude analyzes a call, its step-by-step reasoning (the 'thinking' field "
        "in the JSON response) is stored as <code>cot_reasoning</code> in the database. "
        "The next time the same symbol is analyzed, the previous reasoning is injected "
        "into the prompt as PREVIOUS ANALYSIS context. Claude can then explicitly "
        "compare: 'Last time I analyzed ARKMUSDT, I flagged the SL as too close to "
        "the 4H noise floor. Has that changed?' This enables detection of repeated "
        "mistakes and continuous refinement without any retraining.", S["body"]))
    story.append(PageBreak())

    # ── SECTION 8: AUTO-LINKING ───────────────────────────────────────────────
    story.append(Paragraph("8. Position Auto-Linking System", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "A key feature is that scanner-alerted trades and manually analyzed calls "
        "are automatically linked to the corresponding live positions — without "
        "requiring the user to confirm each match manually.", S["body"]))

    story.append(Paragraph("8.1  How the link is established", S["h2"]))

    link_data = [
        ["Scenario", "Auto-link behaviour"],
        ["Scanner sends Telegram alert for ARKMUSDT Long",
         "scanner_scheduler._persist_setups() saves the setup to analyzed_calls "
         "(analyst='scanner', status='saved'). When ARKMUSDT Long appears in live "
         "positions, check-matches auto-confirms it and sets status='matched'. No "
         "user click required."],
        ["User ran call analyzer for NOTUSDT Long, position closed, then reopened",
         "The call was auto-closed when the position closed. When a new NOTUSDT Long "
         "opens, check-matches detects the closed call + matching position and auto-"
         "reactivates (status='matched'). A 'Previously linked' banner appears in "
         "the UI."],
        ["Trade came from Telegram but no call was ever analyzed (IMXUSDT case)",
         "The live position card shows a yellow '📝 Analyze First' button. Clicking "
         "it navigates to the call analyzer with the symbol pre-filled. After running "
         "and saving the analysis, the link is established automatically."],
        ["Scanner signal didn't save (scored below threshold) but position was opened",
         "A minimal call entry can be created directly from the live position data "
         "with analyst='scanner'. check-matches auto-confirms it on the next cycle."],
    ]
    story.append(make_table(link_data, [4.5*cm, 10.3*cm],
        header_bg=C_ACCENT3, font_size=8.5))

    story.append(Spacer(1, 8))
    story.append(Paragraph("8.2  What appears in Live Trades when linked", S["h2"]))
    story.append(Paragraph(
        "Once a position is linked to a call, the live trades card shows a "
        "<b>Call Targets Panel</b> with: distance from mark price to SL, TP1, TP2, "
        "and the call's average entry. A TP1-reached alert fires when mark price "
        "crosses TP1, with an automatic break-even stop suggestion. "
        "Positions with a linked call also show the setup score, trade type, and "
        "R:R ratio from the original analysis.", S["body"]))
    story.append(PageBreak())

    # ── SECTION 9: ACCURACY & BACKTESTING ─────────────────────────────────────
    story.append(Paragraph("9. Accuracy Measurement & 85% Target", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "The accuracy target of ≥85% means: when the consensus score is ≥6 and "
        "confidence is 'high' (both Claude and Gemini agree within 1 point), the "
        "trade should be profitable at least 85% of the time. This is measured "
        "by <b>scripts/backtest_consensus.py</b>.", S["body"]))

    story.append(Paragraph("9.1  Three hypotheses tested", S["h2"]))

    hyp_data = [
        ["Hypothesis", "Claim", "How measured"],
        ["H1: Claude-only",
         "Score ≥6 from Claude alone predicts a profitable trade",
         "outcome_is_win() for all calls with setup_score ≥ N"],
        ["H2: Consensus",
         "Agreement between Claude and Gemini (|Δ|≤1) lifts accuracy vs H1",
         "outcome_is_win() for calls with consensus_score ≥ N AND confidence='high'"],
        ["H3: Divergence avoidance",
         "Calls with |Δ|>2 (REVIEW flag) have lower win rate than average",
         "outcome_is_win() for calls with |claude_score - gemini_score| > 2"],
    ]
    story.append(make_table(hyp_data, [2.5*cm, 5.5*cm, 6.8*cm],
        header_bg=C_ACCENT, font_size=8.5))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Current status:</b> 5 outcome-recorded calls (need ≥20 for statistical "
        "confidence). The system is now accumulating evidence — every new call saved "
        "stores gemini_score and consensus_score, and every outcome recorded improves "
        "the backtest context injected into future prompts. The 85% target becomes "
        "measurable after ~15-20 more outcome-recorded calls.", S["body"]))

    story.append(Paragraph(
        "Run the backtest at any time: <code>python3 scripts/backtest_consensus.py "
        "--host &lt;pi-ip&gt;:8082</code>. Add <code>--live</code> to re-score the "
        "last 20 calls with Gemini live (uses API credits).", S["body"]))
    story.append(PageBreak())

    # ── SECTION 10: SUMMARY TABLE ─────────────────────────────────────────────
    story.append(Paragraph("10. Complete Agent Reference", S["h1"]))
    story.append(ColoredRule(C_ACCENT))
    story.append(Spacer(1, 4))

    ref_data = [
        ["Agent / Module", "Type", "Model", "Trigger", "Token budget"],
        ["call_analyzer",    "Analysis",    "Sonnet 4.6",     "On demand",        "~4,000 in / 4,096 out"],
        ["scanner (batch)",  "Analysis",    "Sonnet 4.6",     "30 min auto",      "~5,500 in / 14,400 out"],
        ["scanner (quick)",  "Analysis",    "Haiku 4.5",      "Per finalist",     "~1,200 in / 120 out"],
        ["advisor",          "Analysis",    "Sonnet 4.6",     "On demand",        "~4,000 in / 4,096 out"],
        ["rulebook",         "Analysis",    "Sonnet 4.6",     "Weekly / manual",  "~3,000 in / 2,048 out"],
        ["hindsight",        "Analysis",    "Haiku 4.5",      "Batch on demand",  "~800 in / 512 out"],
        ["live_trade",       "Analysis",    "Haiku 4.5",      "Per click",        "~600 in / 768 out"],
        ["trade_grader",     "Analysis",    "Haiku 4.5",      "Per closed trade", "~700 in / 350 out"],
        ["limit_analyzer",   "Analysis",    "Sonnet 4.6",     "Per limit order",  "~2,000 in / 768 out"],
        ["pattern_detector", "Analysis",    "Sonnet 4.6",     "Via advisor",      "~2,500 in / 1,200 out"],
        ["Gemini 2.0 Flash", "External AI", "Gemini 2.0",     "Parallel w/ call", "~300 in / 200 out"],
        ["xAI Grok 3 Fast",  "External AI", "Grok 3",         "Parallel w/ call", "~250 in / 130 out"],
        ["Nansen screener",  "Data",        "—",              "Per scan / call",  "1 API credit per run"],
        ["chart_context",    "Data",        "—",              "Per analysis",     "Bitget REST (cached)"],
        ["market_context",   "Data",        "—",              "Per analysis",     "4 exchanges + 2 APIs"],
        ["scanner_scheduler","Automation",  "—",              "Every 30 min",     "Spawns scanner + Telegram"],
        ["bitget_sync",      "Automation",  "—",              "Every 5 min",      "Bitget REST cursor"],
        ["blofin_sync",      "Automation",  "—",              "Every 5 min",      "Blofin REST cursor"],
    ]
    story.append(make_table(ref_data,
        [3.8*cm, 2.3*cm, 2.5*cm, 2.5*cm, 3.7*cm],
        header_bg=C_ACCENT, font_size=8))

    story.append(Spacer(1, 10))
    story.append(ColoredRule(C_MUTED))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Trading Journal v1.0.1 · Self-hosted on Raspberry Pi 5 · "
        "Built with Claude Code · github.com/anvilfilbert/Auto-Crypto-Tradingjournal",
        ParagraphStyle("footer", fontSize=8, textColor=C_MUTED,
                      fontName="Helvetica", alignment=TA_CENTER)))

    return story


# ── Page template ─────────────────────────────────────────────────────────────
def on_page(canvas, doc):
    canvas.saveState()
    w, h = A4
    # Dark background
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    # Top accent bar
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, h - 6*mm, w, 6*mm, fill=1, stroke=0)
    # Bottom bar
    canvas.setFillColor(C_SURFACE)
    canvas.rect(0, 0, w, 10*mm, fill=1, stroke=0)
    # Page number
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawCentredString(w / 2, 3.5*mm, f"Page {doc.page}")
    canvas.restoreState()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    print(f"Generating PDF → {OUTPUT}")

    doc = SimpleDocTemplate(
        OUTPUT,
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.6*cm,  bottomMargin=1.6*cm,
        title="Trading Journal — AI Agent Architecture",
        author="Trading Journal v1.0.1",
        subject="Multi-Model Intelligence Framework",
    )

    styles = make_styles()
    story  = build_story(styles)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    size_mb = os.path.getsize(OUTPUT) / 1_000_000
    print(f"Done — {size_mb:.1f} MB  →  {OUTPUT}")


if __name__ == "__main__":
    main()
