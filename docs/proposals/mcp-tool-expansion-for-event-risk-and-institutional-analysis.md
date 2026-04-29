# MCP Tool Expansion Proposal for Event Risk and Institutional Analysis

## Executive Summary

This proposal recommends the next wave of MCP tools for the StockPortfolioManager analysis stack. The current toolset is already strong in price-based technical analysis, options-chain structure, sentiment, and stop-loss framing. It is materially weaker in the areas that professional financial-services teams rely on most for earnings and event-risk decisions:

- market expectations before the event
- what the options market has already priced in
- how the stock historically reacts to similar events
- who owns the stock and whether that ownership is crowded
- whether management tone, guidance quality, or segment KPIs are changing

The goal of this work is to move the system from a mostly descriptive analysis engine to an event-aware, expectations-aware, and positioning-aware decision-support platform.

This proposal combines:

- a gap analysis of the current MCP coverage
- recommended new MCP tools
- the practical benefits those tools would provide
- a proposed implementation order
- suggested data sources and schema ideas

Project constraint:

- the roadmap assumes continued use of free and openly available data sources only, including `yfinance`, SEC filings, FINRA datasets, public investor-relations materials, and other public web sources

---

## Why This Matters

The recent `WDC` earnings-risk analysis illustrates both the strength and the current limits of the platform.

What the current MCP stack handled well:

- strong trend and relative strength detection
- options-chain context and unusual call activity
- sentiment and headline-news interpretation
- support, resistance, volatility, and trailing-stop framing
- earnings calendar awareness

What it could not answer directly enough:

- what the Street expected versus what was already priced in
- whether the implied earnings move was rich or cheap relative to history
- whether prior WDC earnings beats still led to selloffs
- whether estimate revisions had raised the bar into the print
- whether the stock was institutionally crowded
- whether insider and ownership behavior supported holding the event risk

Those missing dimensions matter because post-earnings outcomes are driven less by "good company / bad company" and more by "expectations versus results, plus positioning."

---

## Current MCP Coverage

Today the platform has strong support for:

- price trend, Bollinger Bands, VWAP, RSI, MACD, Stochastic, OBV
- candlestick, higher-low, gap, and historical drawdown analysis
- options-chain summaries, unusual calls, delta-adjusted open interest
- short interest, bid/ask spread proxy, dark-pool proxy
- trade recommendations and stop-loss synthesis
- earnings calendar proximity
- news collection and sentiment scoring
- fundamental scoring, revenue trajectory, and earnings acceleration

This is a solid retail-to-prosumer research stack. It is not yet a full event-risk stack.

---

## Gap Analysis vs Professional Workflows

Institutional single-name workflows typically add four classes of information that are not yet adequately represented:

### 1. Expectations

Examples:

- consensus EPS and revenue
- prior guidance versus current consensus
- estimate revision breadth and magnitude
- target-price and rating change momentum

Without this, the platform cannot measure whether a beat was already priced in.

### 2. Event Pricing

Examples:

- implied move from front-week straddles
- implied move percentile versus realized post-earnings moves
- term structure distortion around the event
- expected IV crush after the release

Without this, the platform cannot answer whether holding through earnings is rational relative to priced volatility.

### 3. Ownership and Positioning

Examples:

- 13F concentration and crowding
- recent Form 4 insider activity
- daily short-sale flow
- active-owner concentration and new large holders

Without this, the platform cannot distinguish healthy sponsorship from crowded long exposure.

### 4. Fundamental Read-Through and Management Quality

Examples:

- segment KPI tracking
- peer read-through from adjacent earnings
- transcript tone and guidance confidence
- company-specific guide quality and conservatism

Without this, the platform cannot detect when the real driver of the stock is outside headline EPS.

---

## Recommended New MCP Tools

The tools below are grouped by value area. The first section lists the highest-priority additions.

## Phase 1: Highest-Value Additions

### 1. `implied_move_tool`

Purpose:

- derive expected move from the nearest earnings straddle
- compare current implied move with historical post-earnings realized moves

Why it matters:

- this is one of the most useful missing tools for hold-through-earnings decisions
- it directly answers whether the options market expects more or less movement than history suggests

Suggested outputs:

- `earnings_date`
- `front_expiration`
- `straddle_mid`
- `implied_move_dollars`
- `implied_move_pct`
- `historical_avg_earnings_move_pct`
- `historical_median_earnings_move_pct`
- `implied_vs_historical_ratio`
- `label`: `cheap`, `fair`, `expensive`

Benefits:

- improves event-risk sizing
- explains whether "sell some before earnings" is supported by options pricing
- helps avoid vague language around risk

### 2. `earnings_expectations_tool`

Purpose:

- collect current consensus EPS, revenue, prior company guidance, and recent estimate changes from free and public data sources

Why it matters:

- stocks react to the delta between expectations and results, not just to absolute numbers

Suggested outputs:

- `consensus_eps`
- `consensus_revenue`
- `guidance_eps_low/high`
- `guidance_revenue_low/high`
- `estimate_revision_7d`
- `estimate_revision_30d`
- `bar_to_clear_score`
- `expectations_label`

Benefits:

- lets the system say whether the market has raised the bar into the print
- makes pre-earnings analysis materially more actionable

### 3. `post_earnings_reaction_tool`

Purpose:

- run an event study across the last 8 to 12 earnings reports

Why it matters:

- some stocks sell off after beats and rally after misses because positioning dominates results

Suggested outputs:

- `historical_events`
- `avg_next_day_gap_pct`
- `median_next_day_gap_pct`
- `avg_3d_drift_pct`
- `avg_10d_drift_pct`
- `beat_reaction_summary`
- `miss_reaction_summary`
- `guidance_up_reaction_summary`
- `guidance_down_reaction_summary`

Benefits:

- converts generic risk advice into stock-specific behavioral advice
- helps identify post-earnings announcement drift patterns

### 4. `estimate_revision_tool`

Purpose:

- track analyst estimate and target-price changes over `7`, `30`, and `90` days

Why it matters:

- revision direction is a core institutional signal

Suggested outputs:

- `eps_revision_breadth`
- `revenue_revision_breadth`
- `target_revision_breadth`
- `net_rating_change`
- `revision_acceleration`

Benefits:

- identifies when bullish setups are being undermined by falling expectations
- improves pre-event context without relying on price action alone

### 5. `insider_activity_tool`

Purpose:

- parse SEC Form 4 activity and classify insider transactions

Why it matters:

- insider open-market buying is often more informative than generic news sentiment

Suggested outputs:

- `recent_filings`
- `open_market_buys`
- `open_market_sells`
- `10b5_1_sales`
- `option_exercises`
- `tax_sales`
- `insider_conviction_score`

Benefits:

- adds timely ownership intelligence
- helps distinguish genuine insider conviction from noise

### 6. `institutional_ownership_tool`

Purpose:

- parse 13F ownership and concentration patterns

Why it matters:

- crowded institutional longs often react violently to minor disappointments

Suggested outputs:

- `top_holders`
- `holder_concentration_pct`
- `new_large_holders`
- `net_adds_cuts`
- `crowding_score`
- `ownership_stability_label`

Benefits:

- improves downside-gap risk assessment
- provides context for why good earnings can still fail

### 7. `dealer_gamma_tool`

Purpose:

- estimate dealer gamma positioning by strike and expiry rather than relying only on net DAOI

Why it matters:

- gamma structure often determines whether earnings moves expand, pin, or reverse

Suggested outputs:

- `gamma_flip`
- `call_wall`
- `put_wall`
- `pin_risk_strike`
- `gamma_regime`
- `expected_move_amplification`

Benefits:

- upgrades options positioning analysis toward sell-side style market-structure work

### 8. `transcript_nlp_tool`

Purpose:

- parse earnings call transcripts for tone, uncertainty, KPI mentions, and directional changes versus prior quarters

Why it matters:

- management tone often moves the stock more than the press release

Suggested outputs:

- `tone_score`
- `tone_delta_vs_prior`
- `uncertainty_score`
- `confidence_score`
- `theme_counts`
- `guidance_tone_label`

Benefits:

- makes post-earnings analysis more robust
- supports same-day follow-up recommendations after the conference call

### 9. `segment_kpi_tool`

Purpose:

- track company-specific operating metrics from filings, decks, and transcripts

Examples:

- WDC cloud revenue mix
- HDD exabyte shipments
- pricing trends
- hyperscaler exposure

Why it matters:

- institutions frequently trade the KPI, not the headline EPS

Suggested outputs:

- `kpi_name`
- `current_value`
- `prior_value`
- `trend`
- `surprise_vs_expectation`
- `importance_label`

Benefits:

- adds domain-specific depth for each coverage universe

### 10. `gap_risk_tool`

Purpose:

- model stock-specific overnight event-gap probability using earnings history, realized volatility, sector behavior, and current options pricing

Why it matters:

- this is the most direct missing answer for "should I hold through earnings?"

Suggested outputs:

- `prob_down_5`
- `prob_down_10`
- `prob_up_5`
- `prob_up_10`
- `expected_gap_distribution`
- `event_risk_label`

Benefits:

- turns narrative risk language into quantified event-risk bands

---

## Phase 2: Strong Follow-On Additions

### `vol_surface_tool`

- measures skew, smile shape, and tenor differences
- helps separate directional call buying from expensive upside speculation

### `vol_crush_tool`

- estimates post-event IV compression from prior events
- improves options-hedge and options-avoidance decisions

### `short_flow_tool`

- extends short interest with FINRA daily short-sale volume
- adds more timely squeeze and fade context

### `peer_readthrough_tool`

- maps recent peer earnings to sympathy-move risk
- especially useful in semis, storage, software, and retailers

### `guidance_quality_tool`

- compares company guidance versus consensus and historical guide conservatism
- helps detect "beat and lower" or low-quality beats

### `liquidity_regime_tool`

- estimates open-gap execution risk, slippage, and spread behavior
- useful for realistic stop-loss planning

### `factor_exposure_tool`

- decomposes stock sensitivity to market, sector, rates, and major thematic baskets
- improves interpretation of whether the earnings reaction is idiosyncratic or macro-driven

### `filing_monitor_tool`

- scans 8-K, 10-Q, 10-K, debt, convert, and shelf activity
- surfaces financing and dilution risks that chart tools cannot see

### `news_expectations_tool`

- distinguishes positive news from expectation-raising news
- helps identify when bullish headlines have actually made an earnings setup harder

### `supply_chain_readthrough_tool`

- tracks suppliers, customers, and related companies for read-through signals
- useful for sectors where adjacent earnings are highly informative

### `recent_earnings_reaction_tool`

- scans recent earnings across a market, watchlist, or sector and measures whether beats, misses, and guidance changes were rewarded or punished
- helps identify the current earnings reaction function, which is often more important than the raw result

---

## Comparative Analysis Expansion

Beyond single-name analysis, the platform would benefit from a stronger comparative layer. Professional investors rarely evaluate a stock in isolation. They compare it against peers, sectors, factors, ownership structures, and post-event behavior across similar names.

The most useful comparative categories to add are:

### `peer_relative_value_tool`

- compares a stock against direct peers on valuation, growth, margins, estimate revisions, and momentum
- helps answer whether the stock is actually the best name in the group or simply the most extended

### `peer_reaction_profile_tool`

- compares how the stock has historically reacted versus peers after beats, misses, guide-ups, and guide-downs
- helps identify names that routinely underperform or outperform on similar events

### `sector_regime_comparison_tool`

- compares the sector's current earnings reaction regime with its own history and with other sectors
- helps determine whether a move is stock-specific or part of a broader sector tape

### `factor_exposure_comparison_tool`

- compares the stock's exposure to market, rates, sector, and thematic baskets versus peers
- helps identify whether the name is being driven by company-specific signals or macro/factor forces

### `ownership_crowding_comparison_tool`

- compares insider activity, 13F concentration, short interest, and daily short-flow trends across peers
- helps identify which names are most crowded and therefore most vulnerable to violent post-event air pockets

### `peer_options_positioning_tool`

- compares implied move, IV rank, skew, put/call structure, and gamma concentrations across peers
- helps identify where the market is pricing the most upside or downside risk

### `technical_leadership_tool`

- compares relative strength, VWAP distance, moving-average structure, and volume confirmation across a peer basket
- helps identify the true technical leader rather than the noisiest mover

### `event_drift_comparison_tool`

- compares 3-day, 10-day, and 20-day post-event drift across peer groups
- helps distinguish names that sustain reactions from those that mean-revert quickly

### `management_credibility_comparison_tool`

- compares guidance conservatism, follow-through, and post-call reaction quality across management teams
- helps determine whose guidance the market consistently trusts

### `ecosystem_readthrough_tool`

- compares customers, suppliers, and adjacent ecosystem names to identify where real demand or weakness is showing up first
- helps uncover read-through signals that do not appear in the target company's own charts

Why this matters:

- comparative tools reduce false confidence from looking at one symbol in isolation
- they improve idea selection within a sector, not just trade timing within a name
- they make MCP outputs more aligned with actual portfolio-construction workflows

---

## Automated Pre-Earnings Reporting

In addition to on-demand analysis, the platform should automatically run the relevant pre-earnings tools for current portfolio holdings and generate a report the user can review before the event. This would shift the system from reactive research support to proactive portfolio-risk support.

### Initial Scope

- target universe: current portfolio holdings only
- trigger: pure calendar proximity
- lead time: `T-2` trading days before the earnings announcement
- output: markdown report stored in the project plus surfaced in the UI
- delivery: link sent through the existing Discord notification path
- no separate archival subsystem in v1; report history is retained naturally through the project files and Git

### Proposed Workflow

At `T-2` trading days before a portfolio holding reports earnings:

1. detect the upcoming event from the earnings calendar
2. run the pre-earnings analysis stack for that symbol
3. generate a markdown report in a dedicated project subdirectory
4. surface the report in a new UI tab for earnings-related reports
5. send a Discord notification containing a summary and a link to the report

Recommended v1 report path:

- `docs/analysis results/earnings/`

Recommended filename pattern:

- `{symbol}_pre_earnings_{earnings_date}.md`

### Report Content

The report should summarize the highest-confidence signals and explicitly suppress weak or low-confidence sections.

Core sections:

- earnings timing and event window
- implied move and gap-risk framing
- expectations and estimate revisions
- historical post-earnings reaction profile
- recent peer and sector earnings reaction context
- ownership and crowding context
- options positioning and support/resistance structure
- hold / trim / exit suggestion with risk bands
- confidence score

### Decision Style

The initial version should remain advisory rather than automated.

Recommendation framing:

- summarize the signals
- provide a hold / trim / exit suggestion
- back the suggestion with explicit risk bands and confidence

Future extension:

- when a brokerage with API support is introduced, such as Alpaca, this workflow could become the decision-support layer for semi-automated or automated earnings-risk actions
- the analysis and reporting stack should remain independent of paid market-data assumptions even if execution automation is added later

### Delivery Surfaces

The reports should be accessible in two places:

- directly in the repository under a dedicated subdirectory so the history is naturally retained in Git
- in the UI through a new `Updated Earnings` tab that links to the generated report

The Discord path should send:

- symbol
- earnings date
- top-line suggestion
- confidence
- link to the report

### Freshness and Notification Controls

To keep the workflow operationally safe and avoid noisy duplicates:

- generate at most one pre-earnings report per symbol per earnings event per trading day
- if the report is refreshed, update the existing markdown file rather than creating duplicates
- send the Discord link on first report creation
- only send additional Discord updates if the top-line recommendation, risk band, or confidence changes materially

### Parameterization

Only one user-facing parameter is needed initially:

- lead time before earnings, defaulting to `T-2`

Everything else should stay fixed in the first version to keep the workflow simple and predictable.

### Optional Follow-Up Automation

These are explicitly useful but not required for the initial release:

- next-morning post-earnings action report
- 3-day follow-up drift report

These can be added later once the `post_earnings_reaction_tool` and `event_drift_comparison_tool` are in place.

### Success Criteria

This workflow should be judged primarily on two outcomes:

- fewer bad hold-through-earnings decisions
- better portfolio risk control

Secondary benefits:

- more consistent pre-event review discipline
- better user engagement with the research system

---

## Recommended Build Order

The following sequence gives the best return on implementation time:

1. `implied_move_tool`
2. `earnings_expectations_tool`
3. `post_earnings_reaction_tool`
4. `recent_earnings_reaction_tool`
5. `estimate_revision_tool`
6. `insider_activity_tool`
7. `institutional_ownership_tool`
8. `dealer_gamma_tool`
9. `transcript_nlp_tool`
10. `segment_kpi_tool`
11. `gap_risk_tool`

Rationale:

- the first four tools complete the pre-earnings expectations framework
- the fifth through seventh tools add ownership and crowding context
- the next three deepen market structure and company-specific interpretation
- the final tool synthesizes the others into the clearest user-facing risk model

Implementation note:

- the strict tool sequence above is not the best delivery sequence for the user-facing earnings workflow
- because the stated success criteria are fewer bad hold-through-earnings decisions and better portfolio risk control, the automated `T-2` report should be treated as an MVP delivery track once the minimum report stack exists

Recommended MVP report stack:

1. `implied_move_tool`
2. `earnings_expectations_tool`
3. `post_earnings_reaction_tool`
4. `recent_earnings_reaction_tool`
5. `estimate_revision_tool`
6. `gap_risk_tool`
7. markdown generation, UI surfacing, and Discord delivery

Suggested v1 report behavior:

- include only sections backed by available, high-confidence signals
- degrade gracefully when estimate, guidance, or peer context is incomplete
- explicitly label omitted sections as unavailable rather than silently skipping them

Recommended comparative-analysis priorities after the core event-risk layer:

1. `peer_relative_value_tool`
2. `ownership_crowding_comparison_tool`
3. `peer_options_positioning_tool`
4. `sector_regime_comparison_tool`
5. `event_drift_comparison_tool`

Rationale:

- these five provide the highest-value comparative context with manageable implementation complexity
- they improve security selection, not just signal interpretation

---

## Benefits by Use Case

## Earnings Hold / Sell Decisions

New benefits:

- quantify whether the event is priced for more or less volatility than history
- identify whether consensus revisions have made a beat harder
- detect whether a stock tends to sell off even after good reports
- incorporate ownership crowding and insider behavior into the hold decision

Expected improvement:

- more defensible pre-earnings advice
- fewer false-comfort recommendations based only on bullish momentum

## Post-Earnings Reaction Planning

New benefits:

- detect whether the move is consistent with prior event behavior
- detect whether recent earnings across the sector or market are being rewarded or faded
- compare the stock's reaction with recent peer and sector reactions
- interpret transcript tone separately from release headlines
- frame whether initial gaps are likely to extend or mean-revert

Expected improvement:

- better morning-after sell/hold guidance
- more disciplined reaction plans tied to actual historical behavior
- better awareness of the current sector and market earnings regime

## Portfolio Risk Management

New benefits:

- quantify event risk before earnings instead of treating it like normal volatility
- monitor crowding and ownership deterioration
- improve stop-loss analysis with liquidity and gap realism

Expected improvement:

- fewer avoidable drawdowns from overnight event risk
- stronger distinction between tradable volatility and structural risk

## Institutional-Style Research Quality

New benefits:

- adds expectations, ownership, and transcript analysis to current technical stack
- adds peer, sector, and factor comparison layers that mirror professional research workflows
- makes recommendations closer to the way single-name PMs and analysts frame risk

Expected improvement:

- better internal credibility with experienced investors
- stronger platform differentiation

---

## Suggested Data Sources

Priority sources that are publicly available or practical for early implementation:

### SEC

- Form 4 insider transactions
- Form 13F holdings
- 8-K, 10-Q, and 10-K filings

Benefits:

- authoritative, timely ownership and filing data

References:

- https://www.sec.gov/file/form-13f
- https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f

### FINRA

- daily short-sale volume files

Benefits:

- provides more timely short-flow context than bi-monthly short interest alone

References:

- https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files
- https://www.finra.org/finra-data/browse-catalog/short-sale-volume

### Cboe / Options Data

- VIX term structure references
- options chains and implied-volatility surfaces from existing providers or enhanced feeds

Benefits:

- supports implied-move, term-structure, and skew analysis

Reference:

- https://www.cboe.com/tradable-products/vix/term-structure/

### Existing Market Data Providers

- extend the current equity/options fetch layer where possible using existing free sources first
- prefer public, reproducible data inputs over vendor-specific dependencies

---

## Proposed MCP Shape

To keep the system maintainable, new tools should follow the same pattern as the existing MCP servers:

- one clear analytical responsibility per tool
- structured JSON output with stable field names
- qualitative labels plus raw metrics
- enough context for an LLM to explain the result without re-computation

Suggested conventions:

- `label` for human-readable interpretation
- `score` for normalized directional value
- `confidence` for signal quality
- `as_of_date` for freshness
- `data_note` when the signal is a proxy or incomplete

---

## Example Impact on a WDC-Style Earnings Analysis

With the proposed tools in place, a pre-earnings WDC analysis would improve from:

- strong trend
- bullish call flow
- positive sentiment
- critical event risk

to something closer to:

- implied move is `11.2%`, which is `1.4x` the stock's median realized earnings move
- estimates were revised higher over the last `14` days, raising the bar into the print
- on the last `6` beats, the stock had an average next-day reaction of `-2.8%`
- among the last `20` storage and adjacent infrastructure earnings reports, beats with strong guidance were rewarded while merely in-line results were sold
- ownership is moderately crowded, with top-holder concentration rising
- insiders have not shown recent open-market buying
- peer read-through from `STX` is supportive, but guidance quality risk remains

That is a meaningfully better basis for deciding whether to hold, trim, or exit before earnings.

---

## Implementation Plan

## Phase 1

Build:

- `implied_move_tool`
- `earnings_expectations_tool`
- `post_earnings_reaction_tool`
- `recent_earnings_reaction_tool`
- `estimate_revision_tool`

Outcome:

- complete expectations, event-pricing, and reaction-regime layer

## Phase 2

Build:

- `gap_risk_tool`
- automated `T-2` pre-earnings report generation for portfolio holdings
- markdown report output in `docs/analysis results/earnings/`
- `Updated Earnings` UI tab with report links
- Discord notification links for generated reports
- confidence-based section suppression and hold / trim / exit summary framing

Outcome:

- deliver the first proactive earnings-risk workflow for current portfolio holdings

## Phase 3

Build:

- `insider_activity_tool`
- `institutional_ownership_tool`
- `short_flow_tool`
- `dealer_gamma_tool`

Outcome:

- complete ownership and positioning layer

## Phase 4

Build:

- `transcript_nlp_tool`
- `segment_kpi_tool`
- `guidance_quality_tool`
- `peer_readthrough_tool`

Outcome:

- complete event-interpretation and probabilistic risk layer

## Phase 5

Build:

- `peer_relative_value_tool`
- `ownership_crowding_comparison_tool`
- `peer_options_positioning_tool`
- `sector_regime_comparison_tool`
- `event_drift_comparison_tool`

Outcome:

- complete the first comparative-analysis layer for peer selection, sector context, and cross-sectional ranking

---

## Risks and Constraints

- some estimate histories and target-revision details will remain incomplete under a free-data-only constraint
- 13F data is inherently delayed and should be labeled as such
- daily short-sale volume is useful but easy to misread without the right caveats
- transcript and KPI extraction quality depends on source availability and parsing discipline
- options-market structure tools become much stronger with better intraday or print-level data

These are manageable constraints, but they should be explicit in design and documentation.

---

## Recommendation

Proceed with Phase 1 first. It delivers the biggest analytical improvement for earnings and gap-risk decisions with the least conceptual complexity.

If the team wants one immediate priority beyond the current stack, it should be:

1. `implied_move_tool`
2. `earnings_expectations_tool`
3. `post_earnings_reaction_tool`
4. automated `T-2` pre-earnings report delivery once the minimum report stack is in place

Those tools, followed quickly by automated `T-2` report delivery, would materially improve the quality of earnings hold/sell advice and make the platform more aligned with professional event-driven analysis.

---

## Discussion Questions for the Team

- Which of the recommended tools can be built well enough from existing free and public data?
- Which signals are still useful in proxy form even if they cannot reach institutional-grade precision?
- Should the initial target be better human-readable reports, better MCP primitives, or both?
- Do we want a generic cross-sector framework first, or deeper KPI support for a narrower sector list?
- Which proposed tools should be excluded entirely if they depend too heavily on unavailable proprietary data?

---

## Free-Data Feasibility and Constraints

Research summary:

- the current repo appears to rely primarily on `yfinance`, public news feeds, and local NLP scoring
- `yfinance` already exposes analyst and holdings fields such as `earnings_estimate`, `revenue_estimate`, `eps_trend`, `eps_revisions`, `upgrades_downgrades`, `insider_transactions`, `institutional_holders`, and `sec_filings`
- SEC and FINRA public datasets can cover much of the ownership and filing layer without paid feeds
- the biggest remaining gaps under a free-data-only approach are deeper target-price history, standardized transcript access, and true options trade-print / dealer-position data

### Buildable Now with Existing Free and Public Data

These tools are realistic with the current stack plus public-source parsing:

### `implied_move_tool`

Feasibility: `Yes`

Available inputs:

- current options chains from `yfinance`
- underlying price history already used in the repo

Notes:

- can compute straddle-based implied move from bid/ask or midpoint
- can compare with historical realized earnings moves using price history and earnings dates

Limitations:

- no official OPRA-grade quote feed
- spreads may make very short-dated chains noisy on illiquid names

### `post_earnings_reaction_tool`

Feasibility: `Yes`

Available inputs:

- historical prices from existing market-data flow
- earnings dates from `yfinance` / Yahoo Finance calendar data

Notes:

- event-study logic is fully feasible with free data
- strongest on liquid U.S. equities with consistent earnings calendars

### `recent_earnings_reaction_tool`

Feasibility: `Yes, with universe selection logic`

Available inputs:

- earnings dates from `yfinance` / Yahoo Finance
- historical and recent price reactions from existing market-data flow
- estimate and guidance context from `yfinance`, news, and filings where available

Notes:

- this is feasible without a paid vendor if the scope starts with tracked sectors, watchlists, or liquid U.S. equities
- it is especially useful for distinguishing "good report, bad reaction" regimes from true bullish tapes

Limitations:

- building a high-quality market-wide earnings universe and classifying guide-up / guide-down consistently will take curation

### `estimate_revision_tool`

Feasibility: `Mostly yes`

Available inputs:

- `yfinance` exposes `eps_revisions`, `eps_trend`, `earnings_estimate`, `revenue_estimate`, `growth_estimates`, `recommendations`, and `upgrades_downgrades`

Notes:

- you can build a useful revisions tool from current and recent estimate snapshots
- upgrades/downgrades history can support a practical ratings-change overlay

Limitations:

- target-price history depth may be incomplete
- consensus coverage depends on what Yahoo exposes for a given ticker

### `earnings_expectations_tool`

Feasibility: `Partially yes`

Available inputs:

- current EPS and revenue estimates from `yfinance`
- EPS trend and revisions from `yfinance`
- company guidance from press releases, 8-K exhibits, or investor-relations pages

Notes:

- consensus and revision framing are feasible now
- "bar to clear" logic can be implemented with current estimates plus guidance parsing

Limitations:

- guidance extraction may require company-specific parsing

### `insider_activity_tool`

Feasibility: `Yes`

Available inputs:

- SEC Form 4 filings
- `yfinance` insider transaction and purchase endpoints

Notes:

- this is one of the best candidates for a high-value free-data tool
- SEC XML filings provide enough structure to classify many transaction types

### `institutional_ownership_tool`

Feasibility: `Yes, with delay caveat`

Available inputs:

- SEC Form 13F filings
- `yfinance` institutional, mutual-fund, and major-holder views

Notes:

- concentration, top holders, adds/cuts, and crowding heuristics are feasible

Limitations:

- 13F is delayed by design and excludes shorts
- this is ownership context, not real-time positioning

### `short_flow_tool`

Feasibility: `Yes`

Available inputs:

- FINRA daily short-sale volume files
- existing short-interest tool outputs

Notes:

- this materially improves timeliness versus bi-monthly short interest alone

Limitations:

- FINRA short-sale volume is not the same as short interest
- interpretation needs clear caveats in the output

### `vol_surface_tool`

Feasibility: `Yes`

Available inputs:

- full option chains from `yfinance`

Notes:

- skew, term structure, and smile approximations are feasible now

Limitations:

- quality depends on chain completeness and quote freshness

### `vol_crush_tool`

Feasibility: `Yes, if you start archiving chain snapshots`

Available inputs:

- current repo already stores options snapshots in places
- future snapshot persistence can build the required history

Notes:

- easiest if treated as a forward-looking data-collection project

Limitations:

- cannot fully backfill historical IV crush from free sources if snapshots were not collected

### `gap_risk_tool`

Feasibility: `Yes`

Available inputs:

- historical price gaps
- earnings dates
- implied move
- sector ETF behavior
- existing volatility and drawdown tools

Notes:

- highly feasible once `implied_move_tool` and `post_earnings_reaction_tool` exist

### `factor_exposure_tool`

Feasibility: `Yes`

Available inputs:

- historical prices for stock, SPY, QQQ, sector ETFs, and rates proxies from `yfinance`

Notes:

- straightforward regression and rolling-beta work

### `filing_monitor_tool`

Feasibility: `Yes`

Available inputs:

- SEC filings directly
- `yfinance` `sec_filings` endpoint as a convenience layer

Notes:

- 8-K, 10-Q, 10-K, shelf, convert, and offering detection is realistic with public filings

### `news_expectations_tool`

Feasibility: `Yes`

Available inputs:

- existing news-collection and sentiment stack
- estimate-revision overlays from `yfinance`

Notes:

- can reframe sentiment by asking whether recent coverage likely raised expectations

### `peer_readthrough_tool`

Feasibility: `Yes, with curated peer maps`

Available inputs:

- peer price reactions
- peer earnings dates
- public news and company event calendars

Notes:

- the main work is building and maintaining robust peer-group relationships

### Comparative analysis tools

Feasibility: `Mostly yes`

Buildable now with existing or public data:

- `peer_relative_value_tool`
- `sector_regime_comparison_tool`
- `factor_exposure_comparison_tool`
- `ownership_crowding_comparison_tool`
- `peer_options_positioning_tool`
- `technical_leadership_tool`
- `event_drift_comparison_tool`

Primary inputs:

- `yfinance` price, fundamentals, estimates, recommendations, holders, and options chains
- SEC Form 4 and 13F data
- FINRA daily short-sale volume
- curated peer baskets and sector mappings

Notes:

- these tools are more about data organization and comparison logic than new raw-data acquisition
- the hardest part is maintaining high-quality peer-group definitions and sector relationships

---

### Buildable with Free Data, but Only as a Proxy or Coarser Version

These are feasible, but the first version will be materially less precise than institutional products.

### `dealer_gamma_tool`

Feasibility: `Partial`

Why only partial:

- you can estimate gamma exposure from open interest, strikes, expiries, and implied vol in the option chain
- you cannot observe actual dealer books from public data

What is feasible now:

- gamma wall
- approximate gamma flip
- pin-risk zones

What remains missing:

- true customer trade-side classification
- dealer inventory certainty

### `guidance_quality_tool`

Feasibility: `Partial`

Why only partial:

- guidance often arrives in 8-K exhibits or IR press releases, which are public
- extracting and normalizing that guidance across issuers is messy

What is feasible now:

- compare printed guidance bands to consensus
- measure historical guide conservatism if enough prior guidance is archived

What remains missing:

- clean standardized guidance histories across many issuers

### `segment_kpi_tool`

Feasibility: `Partial`

Why only partial:

- many KPIs are available in earnings decks, prepared remarks, or 10-Qs
- they are inconsistent across companies and sectors

What is feasible now:

- targeted KPI extraction for a small set of covered sectors or names

What remains missing:

- scalable, sector-agnostic normalization

### `transcript_nlp_tool`

Feasibility: `Partial`

Why only partial:

- some companies provide prepared remarks, webcast archives, or transcript-like text in public materials
- others do not provide a clean free transcript feed

What is feasible now:

- parse 8-K earnings releases and public IR materials
- optionally process webcast captions or manually sourced transcripts where available

What remains missing:

- broad, clean, timely, standardized transcript coverage

### `liquidity_regime_tool`

Feasibility: `Partial`

Why only partial:

- daily bars and option quotes can support a coarse liquidity proxy
- true opening-auction quality, NBBO spread dynamics, and slippage modeling require deeper intraday data

What is feasible now:

- high/low range expansion
- option spread proxies
- volume regime changes

What remains missing:

- institutional-grade intraday liquidity modeling

### `supply_chain_readthrough_tool`

Feasibility: `Partial`

Why only partial:

- public filings and news can identify many supplier/customer links
- entity resolution and relationship maintenance are labor-intensive

What is feasible now:

- curated read-through maps for sectors the team actively follows

What remains missing:

- broad, automatically maintained relationship graphs

---

### Out of Scope Under a Free-Data-Only Constraint

These capabilities can be approximated, but they should not be treated as near-term roadmap commitments if they depend on proprietary feeds the project does not plan to adopt.

### Whisper-number support inside `earnings_expectations_tool`

Status:

- out of scope

Why:

- there is no reliable, authoritative, openly available whisper-number source

### Full target-price revision history inside `estimate_revision_tool`

Status:

- limited scope only

Why:

- current targets and partial revision data may be available, but deep historical target-change series are not reliably available from free sources

### Standardized, broad transcript coverage for `transcript_nlp_tool`

Status:

- partial scope only

Why:

- public access to timely, standardized transcripts is uneven across issuers

### Trade-print quality options-flow expansion

Status:

- out of scope

Why:

- the current chain-based proxy cannot fully identify sweeps, spread construction, aggressor side, or true customer flow without proprietary market-data feeds

### High-precision dealer-position analytics

Status:

- out of scope beyond proxy-grade estimates

Why:

- public open-interest snapshots are not the same as real dealer positioning

---

## Practical Recommendation

Based on the current repo and public-source research, the best near-term path is:

### Build immediately with existing data

- `implied_move_tool`
- `post_earnings_reaction_tool`
- `recent_earnings_reaction_tool`
- `estimate_revision_tool`
- `insider_activity_tool`
- `institutional_ownership_tool`
- `short_flow_tool`
- `gap_risk_tool`
- `factor_exposure_tool`
- `filing_monitor_tool`

### Build next, but explicitly label as proxy-grade

- `earnings_expectations_tool` without whisper numbers
- `dealer_gamma_tool`
- `guidance_quality_tool`
- `segment_kpi_tool`
- `transcript_nlp_tool`
- `liquidity_regime_tool`
- `supply_chain_readthrough_tool`

### Explicitly deprioritize or exclude

- whisper-number coverage
- deep target-price revision history
- trade-print / sweep-quality options flow
- any design that assumes proprietary dealer-position data

Bottom line:

- a substantial portion of the proposed roadmap can be built now from `yfinance`, SEC, FINRA, and public IR materials
- the highest-value earnings and gap-risk improvements do not require paid data
- tools that depend heavily on proprietary feeds should either be downgraded to proxy-grade versions or excluded from the near-term roadmap

---

## Conclusion

The current MCP suite is already a strong foundation. The next step is not more chart indicators. The next step is adding the parts of the workflow that professional investors actually use to evaluate event risk: expectations, event pricing, positioning, ownership, and management-quality interpretation.

Adding those capabilities will improve the platform in three important ways:

- better earnings and gap-risk decisions
- better post-event reaction planning
- better differentiation from generic retail trading dashboards

This proposal is intended to help the team decide where to invest next and in what order.
