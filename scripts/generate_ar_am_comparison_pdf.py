"""Generate a combined AR/AM analysis PDF report."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_PATH = (
    Path(__file__).parent.parent
    / "docs"
    / "analysis results"
    / "AR_AM_comparison_2026-05-28.pdf"
)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

styles = getSampleStyleSheet()

H1 = ParagraphStyle(
    name="H1",
    parent=styles["Heading1"],
    fontSize=18,
    spaceAfter=12,
    textColor=colors.HexColor("#0a3d62"),
)
H2 = ParagraphStyle(
    name="H2",
    parent=styles["Heading2"],
    fontSize=14,
    spaceBefore=12,
    spaceAfter=8,
    textColor=colors.HexColor("#0a3d62"),
)
H3 = ParagraphStyle(
    name="H3",
    parent=styles["Heading3"],
    fontSize=11,
    spaceBefore=8,
    spaceAfter=4,
    textColor=colors.HexColor("#333333"),
)
BODY = ParagraphStyle(
    name="Body",
    parent=styles["BodyText"],
    fontSize=10,
    leading=14,
    spaceAfter=6,
)
CAPTION = ParagraphStyle(
    name="Caption",
    parent=styles["BodyText"],
    fontSize=9,
    leading=11,
    textColor=colors.HexColor("#666666"),
    spaceAfter=8,
)


def make_table(data, col_widths=None, header=True):
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(style))
    return t


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

def build_story():
    s = []

    # ----------------------------------------------------------- Title
    s += [
        Paragraph("Antero Resources (AR) &amp; Antero Midstream (AM)", H1),
        Paragraph("Combined Analysis Report — 2026-05-28", BODY),
        Paragraph(
            "This report analyses Antero Resources (AR) and its sister "
            "midstream company Antero Midstream (AM) as a pair. The two "
            "companies share Appalachian basin gas exposure but operate "
            "fundamentally different business models — AR is a commodity-"
            "price-sensitive upstream producer, while AM is a fee-based "
            "tollroad infrastructure operator. Together they form a natural "
            "barbell on the Marcellus/Utica natural gas thesis driven by "
            "LNG export growth and data-center power demand.",
            BODY,
        ),
        Spacer(1, 0.15 * inch),
        Paragraph(
            "Verdicts at a glance: <b>AR</b> = tradeable bounce setup, "
            "wait for technical confirmation (stochastic at 1.22, MACD "
            "still bearish). <b>AM</b> = cleaner entry profile with fresh "
            "MACD bullish crossover, lower volatility, and dividend income. "
            "Both score HIGH-confidence BULL_CALL_SPREAD on the trade "
            "engine.",
            BODY,
        ),
    ]
    s.append(PageBreak())

    # ============================================================ AR
    s += [Paragraph("1. Antero Resources Corporation (AR)", H1)]

    s += [Paragraph("Company &amp; context", H2)]
    s += [Paragraph(
        "Large-cap natural gas and NGL exploration &amp; production company "
        "focused on the Appalachian basin (Marcellus &amp; Utica). "
        "Headquartered in Denver, CO. Market cap $11.1B. Earnings 2026-07-29 "
        "(62 days out, LOW near-term options risk). Major beneficiary of "
        "LNG export growth and grid-scale natural gas demand for data "
        "centers.",
        BODY,
    )]

    s += [Paragraph("Price action &amp; structure", H2)]
    s += [make_table([
        ["Metric", "Value", "Read"],
        ["Price", "$35.89 quote / $35.11 last close", "—"],
        ["20-day BB", "lower 34.81 / mid 37.50 / upper 40.18", "bb_pos 0.20 — approaching lower band"],
        ["VWAP(20)", "$37.58 (−6.57%), 4 bars below", "Structural downtrend"],
        ["Gap structure", "5/27 gap-down unfilled at $35.45–$35.80", "First overhead +1.5%"],
        ["Higher lows (1h)", "None — downtrend/sideways intact", "No reversal structure yet"],
        ["Recent slide", "$38.98 (5/19) → $35.11 (5/27)", "−10% in 6 sessions"],
    ], col_widths=[1.5*inch, 2.2*inch, 2.5*inch])]

    s += [Paragraph("Momentum", H2)]
    s += [make_table([
        ["Indicator", "Reading", "Signal"],
        ["RSI(14)", "35.49", "Neutral, low end"],
        ["MACD", "macd −0.56, signal −0.40, hist −0.16", "Bearish, mildly expanding"],
        ["Stochastic", "K 1.22 / D 11.51", "Extremely oversold — capitulation level"],
        ["Candlesticks", "Hanging man 5/13 + 2 long-legged doji", "Topping / indecision"],
    ], col_widths=[1.5*inch, 2.5*inch, 2.2*inch])]

    s += [Paragraph("Volume &amp; accumulation", H2)]
    s += [Paragraph(
        "OBV falls with price but volume_analysis flags subtle bullish "
        "divergence — accumulation footprint beneath the surface. "
        "No climax bars (slow grind rather than panic). Bid/ask spread "
        "at 106.86% of premium (extreme fear) but <b>narrowing</b> from "
        "elevated levels — bottom signal flagged as 'strong'.",
        BODY,
    )]

    s += [Paragraph("Options positioning — the standout signal", H2)]
    s += [make_table([
        ["Metric", "Value"],
        ["Put/Call ratio (OI)", "22.83 — EXTREME"],
        ["Put OI / Call OI", "31,365 vs 1,374 (22.8× skew)"],
        ["Volume P/C", "0.32 — fresh flow mostly calls"],
        ["Near-term skew", "+21.94 (front-month 22.83 vs mid-term 0.89)"],
        ["Avg IV", "calls 166% / puts 109% (very rich)"],
        ["Put unwinding flag", "YES — existing puts being closed without renewal"],
        ["DAOI net", "−1,036 shares → MM net SHORT delta → buy_on_rally (weak)"],
        ["Gamma wall", "$37.50 (overhead resistance)"],
        ["Delta flip", "$26 (far below)"],
        ["Unusual calls", "4 detected, 1 high-conviction at $37 Jun 5 (vol/OI 1.53, paid above ask)"],
    ], col_widths=[1.8*inch, 4.4*inch])]
    s += [Paragraph(
        "<b>Reading the P/C 22.83:</b> Not simple bearish positioning. "
        "Huge put OI + active call volume + put unwinding suggests "
        "<b>collar/protective-put structure being unwound</b> as fear "
        "fades. Likely institutional longs releasing hedges. "
        "<b>Contrarian-bullish.</b>",
        BODY,
    )]

    s += [Paragraph("Relative strength &amp; macro", H2)]
    s += [make_table([
        ["Metric", "Value"],
        ["12m return vs SPY", "−39.76% (weak)"],
        ["12m return vs XLE", "−52.84% (severely lagging own sector)"],
        ["Sector momentum", "XLE +42.19% over 12m — AR hasn't participated"],
        ["1m / 3m / 6m / 12m", "−6.9% / +4.39% / +3.85% / −10.65%"],
        ["Short interest", "3.9% float, 2.43 days-to-cover — LOW squeeze"],
    ], col_widths=[1.8*inch, 4.4*inch])]

    s += [Paragraph("Fundamentals — solid &amp; accelerating", H2)]
    s += [make_table([
        ["Metric", "Value", "Score"],
        ["Composite score", "+5 (solid)", "✓"],
        ["Revenue 3y CAGR", "−15.5%", "−1 (declining base)"],
        ["Revenue acceleration", "+25.5% YoY", "+1 (accelerating)"],
        ["Op margin 3y", "8.5%", "0"],
        ["Op margin trend", "+12.0% expanding", "+1"],
        ["FCF margin 3y", "30.2% (strong)", "+2"],
        ["Valuation (P/E)", "fair", "+1"],
        ["12-1 momentum", "0.1% (positive)", "+1"],
    ], col_widths=[1.8*inch, 2.6*inch, 1.8*inch])]
    s += [Paragraph(
        "<b>Quarterly revenue trajectory:</b> "
        "$1.39B → $1.20B → $1.13B → $1.28B → <b>$1.86B (+45.1% QoQ in Q1 2026)</b>. "
        "<b>Net income:</b> $208M → $157M → $76M → $194M → <b>$535M (Q1 2026 = 6.9× the trough quarter)</b>. "
        "This is a fundamentally improving company in the middle of an earnings acceleration cycle.",
        BODY,
    )]

    s += [Paragraph("Trade engine output", H2)]
    s += [Paragraph(
        "<b>BULL_CALL_SPREAD, HIGH confidence, net_score +5</b> (bull 9 / bear 4). "
        "Drivers: stochastic 1.22 oversold, OBV bullish divergence, bid/ask "
        "narrowing, fundamentals solid, revenue accelerating, EPS accelerating, "
        "moderate unusual call sweep. Target $40.18 (upper BB), stop $34.11, RR 2.41.",
        BODY,
    )]

    s += [Paragraph("AR verdict — tradeable bounce setup, wait for confirmation", H2)]
    s += [Paragraph(
        "This is the inverse of the IMSR setup: solid fundamentals "
        "(+5) combined with technical capitulation (stoch K 1.22). "
        "Higher-quality risk/reward than most bounce trades. But two reasons "
        "to wait: MACD still bearish &amp; expanding, and high IV (166%) "
        "makes long-premium expensive.",
        BODY,
    )]
    s += [Paragraph(
        "<b>Setups in priority order:</b>",
        BODY,
    )]
    s += [Paragraph(
        "1. <b>Wait-for-confirmation play (preferred)</b> — trigger: AR closes "
        "above $36.55 (5/26 prior close) on volume > 5M. Then bull call spread or stock.<br/>"
        "2. <b>Bull call spread now</b> — Aug 21 2026 $36/$40 call spread; targets upper BB "
        "by earnings (7/29). Work limit toward mid given high IV.<br/>"
        "3. <b>Premium-selling play</b> — cash-secured put at $34 strike, Jul 17 expiration. "
        "Collect premium while waiting for cleaner entry around $34 (lower BB + gamma wall confluence).",
        BODY,
    )]
    s += [Paragraph(
        "<b>Bail:</b> Close at $33 (gamma wall) on rising volume.",
        BODY,
    )]
    s.append(PageBreak())

    # ============================================================ AM
    s += [Paragraph("2. Antero Midstream Corporation (AM)", H1)]

    s += [Paragraph("Company &amp; context", H2)]
    s += [Paragraph(
        "Midstream operator providing gathering, compression, processing, "
        "and water-handling services for Antero Resources' Appalachian "
        "production. Fee-based business model — behaves more like a "
        "utility/income name than a commodity producer. Market cap $10.1B. "
        "Earnings 2026-07-29 (same day as AR). Typical dividend yield ~6%.",
        BODY,
    )]

    s += [Paragraph("Price action &amp; structure", H2)]
    s += [make_table([
        ["Metric", "Value", "Read"],
        ["Price", "$21.19 quote / $21.31 last close", "—"],
        ["20-day BB", "lower 20.93 / mid 21.72 / upper 22.51", "bb_pos 0.24 — lower-middle; very tight bands"],
        ["VWAP(20)", "$21.68 (−1.71%), 1 bar below", "Just broke down, weak reclaim signal"],
        ["Volatility", "Max 1-day drawdown −4.23%; trailing stop 5.54%", "Low-vol stock (vs AR's 11.5%)"],
    ], col_widths=[1.5*inch, 2.4*inch, 2.3*inch])]

    s += [Paragraph("Momentum", H2)]
    s += [make_table([
        ["Indicator", "Reading", "Signal"],
        ["RSI(14)", "43.69", "Neutral"],
        ["MACD", "macd +0.058, signal +0.052, hist +0.006", "FRESH BULLISH CROSSOVER"],
        ["Stochastic", "K 34.04 / D 58.69", "Bearish crossover, K falling"],
        ["Candlesticks", "1 doji (5/19)", "Indecision; no reversal signal"],
    ], col_widths=[1.5*inch, 2.5*inch, 2.2*inch])]

    s += [Paragraph("Volume &amp; accumulation", H2)]
    s += [Paragraph(
        "OBV flat while price falls — no clear accumulation. No climax bars. "
        "Volume 0.85× avg. Bid/ask spread at 44.79% (less extreme than AR) "
        "and <b>narrowing</b> — strong bottom signal flagged.",
        BODY,
    )]

    s += [Paragraph("Options positioning", H2)]
    s += [make_table([
        ["Metric", "Value"],
        ["Put/Call ratio (OI)", "0.22 (bullish)"],
        ["Avg IV", "calls 117% / puts 88% — high for a midstream"],
        ["DAOI net", "+6,193 shares → MM net LONG delta → sell_on_rally"],
        ["Gamma wall", "$22 (just above current — overhead cap)"],
        ["Delta flip", "$10 (far below)"],
        ["Unusual calls", "1 high-conviction sweep at Jul 17 $23 (vol/OI 2.22, +8.5% OTM)"],
    ], col_widths=[1.8*inch, 4.4*inch])]
    s += [Paragraph(
        "<b>Read:</b> Bullish positioning (low P/C) and high-conviction "
        "smart-money flow at $23 strike, but MM hedge bias is sell_on_rally — "
        "mechanical resistance caps the upside near the $22 gamma wall. Means "
        "any bounce is likely to stall in the $22–$23 zone unless something "
        "structurally changes positioning.",
        BODY,
    )]

    s += [Paragraph("Relative strength &amp; macro", H2)]
    s += [make_table([
        ["Metric", "Value"],
        ["12m return", "+17.22% (lagging SPY +29.08% but positive absolute)"],
        ["6m return", "+23.49% (strong)"],
        ["3m / 1m return", "−4.18% / −1.99% (recent consolidation)"],
        ["RS vs sector (XLE)", "−24.92 (sector lagging)"],
        ["RS label", "laggard"],
        ["Short interest", "3.17% float, 3.96 days-to-cover — MEDIUM squeeze risk"],
    ], col_widths=[1.8*inch, 4.4*inch])]
    s += [Paragraph(
        "<b>Note:</b> AM is the only Antero-pair name with MEDIUM squeeze "
        "potential (4.0 days-to-cover). Not high enough to drive the trade, "
        "but a tailwind if volume spikes.",
        BODY,
    )]

    s += [Paragraph("Fundamentals — solid (higher score than AR)", H2)]
    s += [make_table([
        ["Metric", "Value", "Score"],
        ["Composite score", "+7 (solid) — higher than AR's +5", "✓"],
        ["Revenue 3y CAGR", "+8.3% (slow)", "0"],
        ["Revenue acceleration", "+1.2% (stable)", "0"],
        ["Op margin 3y", "56.4% (strong)", "+2"],
        ["Op margin trend", "+2.6% expanding", "+1"],
        ["FCF margin 3y", "88.6% (strong)", "+2"],
        ["Valuation (P/E)", "rich", "0"],
        ["12-1 momentum", "+21.8% (strong)", "+2"],
    ], col_widths=[1.8*inch, 2.6*inch, 1.8*inch])]
    s += [Paragraph(
        "<b>The midstream profile:</b> revenue grows modestly (Q1 2026 $335M, "
        "+6.6% QoQ), op margin 56%, FCF margin 88.6%. This is the "
        "infrastructure-tollroad signature — steady, high-margin, capital-"
        "efficient cash flow generation. Net income is more volatile due to "
        "non-cash items: $124.5M → $116M → $51.9M → $118.3M.",
        BODY,
    )]

    s += [Paragraph("Trade engine output", H2)]
    s += [Paragraph(
        "<b>BULL_CALL_SPREAD, HIGH confidence, net_score +5</b> (bull 6 / bear 1). "
        "Drivers: MACD bullish, unusual call sweep, spread narrowing, "
        "fundamentals solid (+7), revenue accelerating, news moderately positive. "
        "Warnings: MM sell_on_rally bias (caps upside), strong sweeps vs MM resistance. "
        "Target $22.51 (upper BB), stop $20.51, RR 1.9.",
        BODY,
    )]

    s += [Paragraph("AM verdict — cleaner entry than AR, less torque", H2)]
    s += [Paragraph(
        "AM has the more attractive entry profile right now: fresh MACD "
        "bullish crossover (vs AR's bearish), already-positive 12m return, "
        "tighter volatility, dividend income while you wait. The price of "
        "this lower risk is lower upside — the gamma wall at $22 + MM "
        "sell_on_rally bias mechanically caps near-term gains. ",
        BODY,
    )]
    s += [Paragraph(
        "<b>Setups in priority order:</b>",
        BODY,
    )]
    s += [Paragraph(
        "1. <b>Long stock for dividend + bounce</b> — accumulate $20.93–$21.50 "
        "near lower BB. Collect ~6% yield while waiting for re-rate to $22.51. "
        "Stop $20.03 (trailing).<br/>"
        "2. <b>Bull call spread</b> — Jul 17 2026 $21/$23. Smaller range than AR, "
        "matches the high-conviction unusual sweep at the $23 strike.<br/>"
        "3. <b>Cash-secured put sell</b> — $20 strike, Jul 17 expiration. "
        "Collect premium with willingness to take assignment at lower BB.",
        BODY,
    )]
    s += [Paragraph(
        "<b>Bail:</b> Close at $20.03 (trailing stop) on rising volume.",
        BODY,
    )]
    s.append(PageBreak())

    # ============================================================ Comparison
    s += [Paragraph("3. AR vs AM — Comparative Analysis", H1)]

    s += [Paragraph("Side-by-side data", H2)]
    s += [make_table([
        ["Metric", "AR", "AM"],
        ["Business model", "E&P (commodity)", "Midstream (toll road)"],
        ["Market cap", "$11.1B", "$10.1B"],
        ["Price", "$35.89", "$21.19"],
        ["BB position", "0.20", "0.24"],
        ["MACD", "Bearish (hist −0.16)", "FRESH BULLISH (hist +0.006)"],
        ["Stochastic %K", "1.22 (extreme oversold)", "34.04 (mid-range)"],
        ["RSI", "35.5", "43.7"],
        ["Trade engine", "BULL_CALL_SPREAD HIGH +5", "BULL_CALL_SPREAD HIGH +5"],
        ["Fundamentals", "+5 solid", "+7 solid"],
        ["FCF margin", "30%", "88.6%"],
        ["Op margin", "8.5%", "56.4%"],
        ["Revenue acceleration", "+25.5% (Q1 jump)", "+1.2% (steady)"],
        ["RS vs SPY (12m)", "−39.8% (weak)", "−11.9% (laggard)"],
        ["RS vs XLE (12m)", "−52.8%", "−24.9%"],
        ["DAOI bias", "buy_on_rally (weak)", "sell_on_rally"],
        ["Unusual calls", "Mod, 1 hi-conv @ $37", "Mod, 1 hi-conv @ $23"],
        ["Squeeze potential", "LOW", "MEDIUM"],
        ["Trailing stop", "11.5% (high vol)", "5.5% (low vol)"],
        ["Avg IV", "166% (extreme)", "117% (high)"],
        ["Dividend yield", "Minimal", "~6%"],
        ["Earnings date", "2026-07-29", "2026-07-29 (same day)"],
    ], col_widths=[2.0*inch, 2.0*inch, 2.0*inch])]

    s += [Paragraph("Pair-trade thesis", H2)]
    s += [Paragraph(
        "AR and AM together capture two distinct expressions of the same "
        "Appalachian natural gas thesis. AR provides commodity-price torque "
        "— if Henry Hub spot rises on LNG demand or data-center load, AR's "
        "revenue and FCF expand directly. AM provides infrastructure "
        "income — its fees scale with throughput volume, not price, so it "
        "performs even in flat or declining commodity environments.",
        BODY,
    )]
    s += [Paragraph(
        "<b>The complementarity:</b> AR has more upside in a natgas rally "
        "(higher beta to commodity price, higher torque on margins). AM has "
        "more downside protection in a chop (fee-based revenue + dividend "
        "yield + lower beta). A barbell across both reduces the binary "
        "outcome of being wrong on natgas direction while still expressing "
        "constructive Appalachian basin exposure.",
        BODY,
    )]
    s += [Paragraph(
        "<b>Volume of mutual gas flow:</b> AM's contracted minimum-volume "
        "commitments from AR mean its top-line is partly insulated from AR's "
        "production decisions. But if AR were to materially curtail (e.g. "
        "low natgas prices, capex cuts), AM's growth would slow.",
        BODY,
    )]

    s += [Paragraph("Recommended allocations", H2)]
    s += [make_table([
        ["Profile", "AR weight", "AM weight", "Rationale"],
        ["Aggressive bull", "70%", "30%", "Maximize commodity torque; accept volatility"],
        ["Balanced barbell", "50%", "50%", "Equal-weight torque and income"],
        ["Income-tilted", "30%", "70%", "Prioritize dividend yield + downside protection"],
        ["Pure speculation", "100%", "0%", "Bounce trade only; not for hold"],
        ["Pure income", "0%", "100%", "Dividend-focused; no commodity bet"],
    ], col_widths=[1.4*inch, 0.9*inch, 0.9*inch, 2.8*inch])]

    s += [Paragraph("Risks &amp; failure modes", H2)]
    s += [Paragraph(
        "<b>1. Natural gas price collapse</b> — both names lose, but AR loses "
        "more. AM's fee-based revenue would still print but growth would "
        "stall; AR's earnings would deteriorate rapidly.<br/>"
        "<b>2. Q2 2026 earnings disappointment (7/29)</b> — both report on the "
        "same day. AR more sensitive to commodity-driven beats/misses; AM more "
        "sensitive to throughput volume guidance.<br/>"
        "<b>3. Sector rotation out of energy</b> — XLE has been leading; if it "
        "rolls over, both AR and AM follow even if their fundamentals stay intact.<br/>"
        "<b>4. AR-specific operational issues</b> — well productivity, midstream "
        "constraints, hedge book mark-to-market. These are idiosyncratic risks "
        "AM doesn't share.<br/>"
        "<b>5. AM dividend coverage</b> — if free cash flow compresses, the "
        "~6% yield could be cut. Recent FCF margin of 88.6% gives a wide "
        "cushion but not infinite.",
        BODY,
    )]

    s += [Paragraph("4. Conclusion", H1)]
    s += [Paragraph(
        "Both AR and AM are high-quality setups for a year-end constructive "
        "natural gas thesis. The trade engine independently rated both as "
        "HIGH-confidence BULL_CALL_SPREAD with identical +5 net scores, "
        "but for different reasons: AR is capitulation-oversold with "
        "accelerating fundamentals waiting for a technical turn, AM is "
        "MACD-confirming with a cleaner entry but capped upside.",
        BODY,
    )]
    s += [Paragraph(
        "<b>If forced to pick one</b>: AR for the higher max-profit potential "
        "if the natgas thesis plays out, AM for the higher probability of a "
        "modest positive outcome with much less downside risk. <b>If barbell "
        "allocation is available</b>: balanced 50/50 is the most defensible.",
        BODY,
    )]
    s += [Paragraph(
        "<i>This is research output from a personal dashboard. Not financial "
        "advice. The IV-rank 'extreme fear' tag in the options scorer is "
        "currently degenerate across most names and was de-weighted in this "
        "analysis. Earnings risk (2026-07-29) sits inside typical position "
        "horizons; size accordingly.</i>",
        CAPTION,
    )]

    return s


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="AR & AM Combined Analysis — 2026-05-28",
        author="StockPortfolioManager analysis dashboard",
    )

    doc.build(build_story())
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
