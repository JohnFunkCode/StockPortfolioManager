Here is the full contents of CompositScoreExperiment.md:

⸻

CompositScoreExperiment

Overview

This document describes the composite scoring model implemented in CompositScoreExperiment.py.

The model ranks companies within a watchlist using only data available from the yfinance library.
It blends Growth, Quality, Valuation, and Momentum factors into a single composite score.

All metrics are standardized using cross-sectional Z-scores before aggregation.

⸻

Visual Formula Summary

Composite Score

Score = \frac{\sum w_i \cdot Z_i}{\sum w_i \text{ (for available metrics)}}

Where:
	•	Z_i = \frac{X_i - \mu_i}{\sigma_i}
	•	For penalty metrics:
Z_i = -\left(\frac{X_i - \mu_i}{\sigma_i}\right)

Penalty metrics invert sign so that higher Z always means better.

⸻

Metric Formula Overview

Revenue CAGR (3Y)

RevCAGR3Y = \left(\frac{R_t}{R_{t-3}}\right)^{1/3} - 1

⸻

Revenue Acceleration

YoY_t = \frac{R_t}{R_{t-1}} - 1
YoY_{t-1} = \frac{R_{t-1}}{R_{t-2}} - 1
RevAccel = YoY_t - YoY_{t-1}

⸻

Quarterly Revenue Volatility (QoQVol4)

g_i = \frac{Q_i}{Q_{i-1}} - 1
QoQVol4 = std(g_{t-3}, g_{t-2}, g_{t-1}, g_t)

Requires 5 quarterly revenue observations.

⸻

Operating Margin

OM_y = \frac{OperatingIncome_y}{Revenue_y}
OpMargin3Y = mean(OM_t, OM_{t-1}, OM_{t-2})

⸻

Operating Margin Trend

OpMarginTrend = OM_t - mean(OM_{t-1}, OM_{t-2})

⸻

Free Cash Flow Margin

FCF_y = CFO_y - Capex_y
FCFMargin_y = \frac{FCF_y}{Revenue_y}
FCFMargin3Y = mean(last\ 3\ years)

⸻

Valuation Metric

Primary:
ValMetric = \log\left(\frac{EnterpriseValue}{Revenue}\right)

Fallback:
ValMetric = \log(PE)

Lower is better (penalty metric).

⸻

Momentum (12–1)

Mom12\_1 = \frac{P_{t-21}}{P_{t-252}} - 1

Excludes the most recent month to reduce short-term mean reversion noise.

⸻

Detailed Metric Explanations

1. Revenue CAGR (3-Year) — Weight 0.20

Measures sustained top-line growth over multiple years.
Captures structural expansion rather than single-year spikes.

Why 20%?
Growth is necessary for long-term compounding but not sufficient alone.

⸻

2. Revenue Acceleration — Weight 0.10

Measures whether revenue growth is speeding up or slowing down.

Why 10%?
Acceleration influences multiple expansion but is more volatile than level growth.

⸻

3. Quarterly Volatility (QoQVol4) — Weight 0.05 (Penalty)

Measures stability of quarterly revenue growth.

Lower volatility suggests execution consistency.

Why 5%?
Penalizes instability without overly punishing cyclicals.

⸻

4. Operating Margin (3-Year Avg) — Weight 0.15

Measures structural profitability and competitive advantage.

Why 15%?
Profitability quality is nearly as important as growth.

⸻

5. Operating Margin Trend — Weight 0.10

Measures margin improvement or deterioration.

Why 10%?
Improving margins signal operating leverage and improving economics.

⸻

6. Free Cash Flow Margin (3-Year Avg) — Weight 0.10

Measures how efficiently revenue converts into cash.

Why 10%?
Cash conversion protects against accounting distortions.

⸻

7. Valuation Metric — Weight 0.20 (Penalty)

Uses EV/Sales primarily; falls back to trailing P/E.

Lower valuation increases expected forward returns.

Why 20%?
Starting valuation materially impacts long-term returns and prevents overpaying for growth.

⸻

8. Momentum (12–1) — Weight 0.10

Captures sustained institutional accumulation over the past year (excluding last month).

Why 10%?
Improves correlation with forward returns without overpowering fundamentals.

⸻

Worked Numerical Example

Assume a stock has:

RevCAGR3Y = 0.30
RevAccel = 0.05
QoQVol4 = 0.08
OpMargin3Y = 0.25
OpMarginTrend = 0.02
FCFMargin3Y = 0.18
ValMetric = 2.5
Mom12_1 = 0.40

Assume universe stats:

Mean RevCAGR3Y = 0.15, Std = 0.10
Mean ValMetric = 3.0, Std = 0.50

Z calculations:

Z_{RevCAGR3Y} = (0.30 - 0.15)/0.10 = +1.5

Z_{ValMetric\ raw} = (2.5 - 3.0)/0.50 = -1.0

Since valuation is a penalty metric:

Z_{ValMetric} = -(-1.0) = +1.0

Assume weighted sum of Z’s = 0.85
Coverage = 1.0

Final Score = 0.85

Interpretation:
The stock ranks meaningfully above the universe average across major dimensions.

⸻

Design Rationale

The model balances four structural drivers of long-term equity returns:
	1.	Growth — Revenue expansion drives intrinsic value.
	2.	Quality — Margins and cash flow ensure growth is durable.
	3.	Valuation — Entry price determines forward return distribution.
	4.	Momentum — Institutional capital flows reinforce trends.

Weight Structure:
	•	Growth + Acceleration = 30%
	•	Quality (Margins + FCF) = 35%
	•	Valuation = 20%
	•	Momentum = 10%
	•	Stability penalty = 5%

Design Intent:
	•	Avoid pure growth chasing.
	•	Avoid value traps.
	•	Avoid momentum crowding.
	•	Favor durable, profitable, reasonably priced compounders with positive trend support.

⸻

Important Notes
	•	Scores are relative to the current watchlist universe.
	•	Z-scores change as the universe composition changes.
	•	Missing metrics reduce Coverage but do not break the model.
	•	yfinance data is not point-in-time safe for backtesting.
	•	This is a ranking model, not a standalone valuation system.

