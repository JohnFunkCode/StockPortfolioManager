# Repeatable Watchlist Stock Research Plan

## Purpose

Repeat the daily watchlist review for stocks that may be candidates for a $1,000–$5,000 position held over a multi-week or longer horizon. The output is a research shortlist, not a trade instruction.

Use the remote stock-price and company-fundamentals MCP servers so that prices, technical levels, options positioning, trade recommendations, and cached fundamentals come from the same analysis workflow.

## Inputs and operating rules

- Start with the current watchlist.
- Keep symbols whose cached composite fundamentals score is greater than 1.
- Use a maximum position size of $5,000.
- Process `get_trade_recommendation` in batches of exactly 3 symbols to avoid timeouts.
- After each batch, compare the new recommendations with the current top-20 ranking. If the three symbols do not change the top 20, state that and skip repeating the full list.
- Link every displayed symbol to Yahoo Finance using this format: `[TICKER](https://finance.yahoo.com/quote/TICKER/)`.
- Record the analysis date and note that prices, options positioning, and recommendations are time-sensitive.

## Step 1 — Build the fundamentals-qualified universe

1. Retrieve the watchlist symbols.
2. Retrieve cached fundamentals scores for the watchlist, preferably with `get_fundamental_scores_batch`.
3. Filter to symbols with `composite_score > 1`.
4. Preserve the score, label, coverage, and any material metric warnings for later ranking.
5. Display the complete fundamentals-qualified universe or, at minimum, its count and the symbols being processed.

The first required intermediate output is:

> **Fundamentals-qualified watchlist:** the list of all symbols with a fundamentals score greater than 1.

## Step 2 — Evaluate trade recommendations in batches

For every fundamentals-qualified symbol, call the remote `get_trade_recommendation` tool with `capital: 5000`.

Process three symbols per call sequence:

```text
Batch 1: symbols 1–3
Batch 2: symbols 4–6
Batch 3: symbols 7–9
...
```

Capture, when available:

- Overall recommendation and signal score
- Entry range, target, stop, and risk/reward
- Technical, volume, options, dark-pool, short-interest, and news components
- Earnings timing and other near-term event risks
- Any tool failures or missing data

Maintain a running ranking across all completed batches. After each batch:

- Re-rank the top 20 candidates.
- If none of the three newly processed symbols enters or changes the top 20, say: **“The three symbols did not change the top 20 ranking.”** Do not repeat the full top-20 table.
- Otherwise, show the updated top 20 with a brief reason for each ranking.

The second required intermediate output is:

> **Top 20 candidates:** symbols ranked using the trade recommendation together with fundamentals, risk/reward, technical confirmation, options positioning, and event risk.

Use judgment to avoid ranking a stock highly solely because it has a large distance to resistance. A support level very close to the current price can artificially inflate the upside/downside ratio.

## Step 3 — Create the further-research list

From the top-20 candidates, create a smaller list of symbols to research further. Exclude symbols with near-term risks such as:

- Earnings or another binary corporate event imminent
- Deteriorating or contradictory fundamentals
- A weak or bearish trade recommendation
- A stop or downside level that is too close for the intended holding period
- Poor liquidity, unusually wide spreads, or unreliable options data
- Excessive concentration with another selected symbol or sector exposure
- A gamma wall or nearby resistance that leaves little room for the proposed entry

For every exclusion, briefly state the reason. For every retained symbol, state why it remains under consideration.

The third required intermediate output is:

> **Symbols to research further:** the retained shortlist, with near-term exclusions and reasons documented separately.

## Step 4 — Compare returns for the research list

For the retained research list, retrieve and display:

- 5-day return
- 30-day return
- 3-month return

Use the remote price-history/returns capability available in the stock-price MCP server. Sort the table by 30-day return unless the user requests another ordering.

Required format:

| Symbol | 5-day return | 30-day return | 3-month return |
|---|---:|---:|---:|
| `[TICKER](https://finance.yahoo.com/quote/TICKER/)` | ... | ... | ... |

Explain that historical returns describe what happened over the selected windows; they are not expected returns. Call out any sharp divergence between short-term and three-month performance.

## Step 5 — Retrieve support, resistance, and gamma-wall data

For every symbol in the research list, call the remote composite support/resistance tool, preferably `get_support_confluence`, and retrieve the gamma-wall contributor from the same options-positioning analysis.

Use the strongest support and strongest resistance zones returned by the tool. When a zone has a range, use its reported center for the table and preserve the range in a note if it is material.

Calculate:

```text
Current − support = current price − strongest support center
Resistance − current = strongest resistance center − current price
Upside / downside = (resistance − current) / (current − support)
Gamma wall − price = gamma wall − current price
```

Interpretation:

- Positive `Gamma wall − price`: gamma wall is above the current price.
- Negative `Gamma wall − price`: gamma wall is below the current price.
- `N/A`: no detectable gamma-wall level was returned; do not infer one from an unrelated resistance level.
- A high upside/downside ratio can be misleading when support is only marginally below the current price.

Required final table:

| Symbol | Current Price | Support | Current − support | Resistance | Resistance − current | Upside / downside | Gamma wall | Gamma wall − price | Fundamentals score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `[TICKER](https://finance.yahoo.com/quote/TICKER/)` | ... | ... | ... | ... | ... | ... | ... | ... | ... |

The user may request the table without the fundamentals column; if so, retain the same calculations and omit only that column.

## Step 6 — Interpret the shortlist

Summarize the data in plain language:

- Distinguish the largest absolute upside from the best upside/downside ratio.
- Identify whether support is close enough to make the ratio potentially misleading.
- Note whether the gamma wall is likely to cap near-term upside or sits below price as a possible positioning reference.
- Reconcile technical attractiveness with fundamentals and trade-recommendation risks.
- Identify the one or two highest-priority names for deeper research, without presenting the output as personalized financial advice.

## Final quality checks

- Confirm every displayed symbol is linked to Yahoo Finance.
- Confirm all symbols in the final table came from the further-research list.
- Confirm calculations use the same current-price snapshot as the support/resistance and gamma-wall data, or clearly label refreshed prices.
- Confirm the fundamentals score is cached/current and include its date or freshness when available.
- Confirm near-term earnings and other event risks were checked before retaining a symbol.
- Clearly flag missing, stale, or failed MCP data.
- Do not treat technical resistance as a forecast or a guarantee of upside.

