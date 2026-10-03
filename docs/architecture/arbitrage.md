# Arbitrage scanner

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

Finds securities whose price has stretched against a structurally linked underlying, across
three families: **nav_vehicle** (treasury companies/trusts — the only family with a computable
fair value), **commodity_etf** (fund vs its reference future), and **producer** (miner/E&P vs
the commodity it sells). Curated links live in **`arb_universe.yaml`** at the repo root
(alongside `watchlist.yaml`); `discover_pairs` additionally sweeps for undeclared cointegrated
links against a reference panel, gated by a sector/industry economic-link filter.

**The scoring is deliberately inverted: spread width only qualifies a candidate, the
convergence mechanism ranks it.** `ArbitrageService._score` multiplies named factors —
`opportunity × evidence × convergence × hedge × carry × trend × freshness` — all returned in
the `factors` block alongside `reasons` and `breaks_on`, so any score is attributable. The design
principle is stated in the `ArbitrageService` module docstring; the penalties are applied in
six numbered steps in `_score`, and every one of them emits a `reasons` entry explaining
itself (plus a `breaks_on` entry where it names a way the trade fails), so the scorer is its
own documentation rather than pointing at a write-up that can drift from it.
`tests/test_arbitrage_nav.py` is a regression guard that the MSTR inputs still produce a ~10%
**net** discount rather than the ~37% headline against gross assets. Because the account is
equity/ETF-only, any pair whose sole clean hedge is a futures contract is flagged
`hedge_available: false` and halved.

Surfaced as `GET /api/arbitrage/{universe,scan,discover,pairs/{security}}` and its own MCP
wrapper `fastMCPTest/arbitrage_server.py` (`arbitrage-server`, port 6007 locally,
`quantcore-arbitrage` on Cloud Run) carrying `list_arbitrage_universe`,
`analyze_arbitrage_pair`, `scan_arbitrage`, `discover_arbitrage_pairs` — one domain per
server, like the others. Expect most scans to return nothing above `watch` — that is the
intended behaviour, not a bug.

**Driving it:** [`docs/arbitrage-scanner-usage.md`](../../docs/arbitrage-scanner-usage.md) — example
prompts per tool, how to read the `factors` breakdown, the MSTR worked example (gross vs net
discount), and how to add a pair to `arb_universe.yaml`.
