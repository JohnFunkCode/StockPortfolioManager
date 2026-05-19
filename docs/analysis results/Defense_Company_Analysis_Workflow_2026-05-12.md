# Defense Company Analysis Workflow (2026-05-12)

## Purpose

This document captures the repeatable workflow used to build and analyze a defense-company stock universe in `docs/analysis results/defense_report.html`.

The goal was to move from a broad defense watchlist to a focused set of actionable candidates by combining price momentum, fundamental quality, technical signals, options context, market-structure signals, earnings risk, and news sentiment.

## Inputs

- `defense_watchlist.yaml` — the source universe of publicly traded defense-related companies.
- `docs/analysis results/defense_report.html` — the generated HTML report containing current prices, return history, fundamentals, screens, and deep analysis.
- MCP tool servers:
  - `company_fundamentals_server`
  - `stock_price_server`
  - `options_analysis_server`
  - `market_analysis_server`
  - `news_sentiment_server`

## Step-by-Step Workflow

### 1. Build the Defense Watchlist

Create `defense_watchlist.yaml` using the same structure as `watchlist.yaml`:

```yaml
- name: Lockheed Martin
  symbol: LMT
  currency: USD
  tags:
    - United States
    - Aircraft
    - Missiles
    - Space systems
```

Use simple string tags only. Do not use dictionary-like tag syntax such as `Country: United States` or `Role: Missiles`.

The watchlist should include:

- U.S. defense primes, services firms, aerospace suppliers, nuclear-defense suppliers, and defense software companies.
- European defense primes, suppliers, shipbuilders, electronics firms, and missile/air-defense companies.
- Country tags and role tags for later human-readable context.

Validate the YAML:

```bash
python -c "import yaml; records=yaml.safe_load(open('defense_watchlist.yaml')); print(len(records))"
```

### 2. Generate the Base HTML Report

Run the application against the defense watchlist to generate the base report with current prices and historical returns.

The resulting report for this run was:

```text
docs/analysis results/defense_report.html
```

The base report provided the first table, `watchlist`, with columns such as current price, today's change, 5-day return, 30-day return, YTD return, 1-year return, and moving averages.

### 3. Add Human-Readable Watchlist Context

Append a `Defense Watchlist Foundation` section near the top of `defense_report.html`.

This section translates `defense_watchlist.yaml` into a sortable table with:

- Company
- Symbol
- Currency
- Country
- Defense exposure / role tags

Purpose: give the reader context before the price, return, fundamentals, and actionability analysis.

### 4. Add Fundamentals Analysis

Run the fundamentals batch analysis for all symbols in `defense_watchlist.yaml`:

```python
get_fundamental_scores_batch(symbols)
```

Append a sortable `Fundamentals Analysis` table to the HTML report with:

- Rank
- Name
- Symbol
- Currency
- Country
- Composite Score
- Fundamental Label
- Coverage
- Sector
- Market Cap
- Revenue CAGR 3Y
- Revenue Acceleration
- Operating Margin 3Y
- Operating Margin Trend
- FCF Margin 3Y
- Valuation
- Momentum 12-1
- Tags

Use the MCP fundamentals cache as the source of truth for composite score and metric labels.

### 5. Screen for Positive Momentum and High Fundamentals

Join the base `watchlist` table with the `fundamentals` table by symbol.

Apply this screen:

```text
30 day Return > 0
Composite Score >= 7
```

For this run, the screen produced 8 companies:

| Symbol | Company | 30 Day Return | Composite Score |
|--------|---------|---------------|-----------------|
| ASELS.IS | Aselsan | 30.61% | 9 |
| GE | GE Aerospace | 6.97% | 9 |
| RR.L | Rolls-Royce Holdings | 10.02% | 8 |
| SAF.PA | Safran | 2.41% | 8 |
| CW | Curtiss-Wright | 13.36% | 7 |
| BWXT | BWX Technologies | 4.28% | 7 |
| HAG.DE | Hensoldt | 1.39% | 7 |
| GD | General Dynamics | 0.28% | 7 |

Append these to the report as a sortable `Positive 30-Day Return + High Fundamentals` section.

### 6. Run Deep MCP Analysis on Finalists

Run deep analysis on the 8 finalists using a `$10,000` capital basis for trade recommendation and position sizing.

Primary synthesis tool:

```python
get_trade_recommendation(symbol, capital=10000)
```

Supporting tools used where available:

- Fundamentals:
  - `get_full_fundamental_profile`
  - `get_fundamental_history`
- Technicals:
  - `get_stock_price`
  - `get_rsi`
  - `get_macd`
  - `get_stochastic`
  - `get_obv`
  - `get_vwap`
  - `get_volume_analysis`
  - `get_candlestick_patterns`
  - `get_higher_lows`
  - `get_gap_analysis`
  - `get_relative_strength`
  - `get_historical_drawdown`
  - `get_stop_loss_analysis`
  - `get_trade_recommendation`
- Options:
  - `analyze_options_symbol`
  - `get_unusual_calls`
  - `get_delta_adjusted_oi`
- Market structure:
  - `get_short_interest`
  - `get_dark_pool`
  - `get_bid_ask_spread`
- News:
  - `collect_news`
  - `get_news_sentiment`
  - `get_sentiment_trend`

Treat missing data, especially for non-U.S. listings, as unavailable rather than bearish.

### 7. Rank by Actionability

Rank the finalists using a composite human judgment from:

- Trade type
- Action
- Confidence
- Net score
- Risk/reward
- Fundamental score and trajectory
- 30-day return
- Relative strength
- Earnings risk
- Options flow and dealer positioning
- Technical trend and reversal signals
- News sentiment
- Liquidity, short interest, spread, and dark-pool proxy signals

For this run, the final actionability ranking was:

| Rank | Symbol | Company | Actionability Summary |
|------|--------|---------|----------------------|
| 1 | CW | Curtiss-Wright | Strongest tactical setup; best overall signal stack |
| 2 | GE | GE Aerospace | Strong fundamentals and bullish flow, but mixed tactical score |
| 3 | RR.L | Rolls-Royce Holdings | High-potential trend setup, but data-limited |
| 4 | BWXT | BWX Technologies | Contrarian setup; needs VWAP/MACD confirmation |
| 5 | GD | General Dynamics | High-quality watchlist name, but no current edge |
| 6 | SAF.PA | Safran | Fundamentally strong, tactically weaker |
| 7 | ASELS.IS | Aselsan | Excellent momentum, but overextended and not chaseable |
| 8 | HAG.DE | Hensoldt | Solid fundamentals, weakest current actionability |

Append the final ranked write-up as `Deep Actionability Analysis`.

## Output Artifacts

The completed HTML report contains these sections:

1. `Defense Watchlist Foundation`
2. `Watchlist`
3. `Fundamentals Analysis`
4. `Positive 30-Day Return + High Fundamentals`
5. `Deep Actionability Analysis`

Primary output:

```text
docs/analysis results/defense_report.html
```

Workflow documentation:

```text
docs/analysis results/Defense_Company_Analysis_Workflow_2026-05-12.md
```

## Validation Checklist

Validate the source watchlist:

```bash
python -c "import yaml; records=yaml.safe_load(open('defense_watchlist.yaml')); assert isinstance(records, list); print(len(records))"
```

Validate report table row counts with a small HTML parser:

```bash
python - <<'PY'
from pathlib import Path
from html.parser import HTMLParser

class TableParser(HTMLParser):
    def __init__(self, table_id):
        super().__init__()
        self.table_id = table_id
        self.in_table = False
        self.in_cell = False
        self.current_cell = []
        self.current_row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == self.table_id:
            self.in_table = True
        elif self.in_table and tag in ("td", "th"):
            self.in_cell = True
            self.current_cell = []
        elif self.in_table and tag == "tr":
            self.current_row = []

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        if self.in_table and tag in ("td", "th"):
            self.current_row.append(" ".join("".join(self.current_cell).split()))
            self.in_cell = False
        elif self.in_table and tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
        elif self.in_table and tag == "table":
            self.in_table = False

text = Path("docs/analysis results/defense_report.html").read_text()
for table_id in ["watchlistFoundation", "watchlist", "fundamentals", "positiveFundamentals", "deepActionability"]:
    parser = TableParser(table_id)
    parser.feed(text)
    print(table_id, "headers=", len(parser.rows[0]), "rows=", len(parser.rows) - 1)

assert text.count("<script>") == 1
assert text.count("</body>") == 1
assert text.count("</html>") == 1
PY
```

Expected row counts from this run:

| Table ID | Rows |
|----------|------|
| `watchlistFoundation` | 43 |
| `watchlist` | 43 |
| `fundamentals` | 43 |
| `positiveFundamentals` | 8 |
| `deepActionability` | 8 |

## Known Limitations

- Non-U.S. listings often have sparse options, earnings, news, or relative-strength data in U.S.-centric MCP tools.
- Missing data should be labeled explicitly and not treated as bearish by default.
- A wide parallel low-level technical batch hit an OS `Too many open files` limit in the stock-price MCP server. Future runs should:
  - use `get_trade_recommendation` first as the synthesis layer,
  - batch lower-level tool calls conservatively,
  - prioritize targeted follow-up calls for finalists.
- The dark-pool signal is a proxy based on price-volume divergence, not true paid dark-pool print data.
- The analysis is a research workflow, not financial advice.

## Repeat Checklist

1. Update or rebuild `defense_watchlist.yaml`.
2. Validate YAML structure and tag style.
3. Generate the base HTML report from the watchlist.
4. Add or refresh `Defense Watchlist Foundation`.
5. Run `get_fundamental_scores_batch` across all watchlist symbols.
6. Append or refresh the sortable fundamentals table.
7. Screen for:
   - `30 day Return > 0`
   - `Composite Score >= 7`
8. Run deep MCP analysis on the screened finalists with the chosen capital basis.
9. Rank finalists by actionability, not just raw score.
10. Validate report table row counts and HTML structure.
11. Record limitations and data gaps in the final report.
