"""Chat sidekick tool schemas and UI-directive validation.

Pure data + pure functions — no service imports, no I/O, no Anthropic SDK.
The Anthropic tool list for the /api/chat agent loop lives here, alongside the
backend copy of the UI component registry that ``show_component`` directives
are validated against before they are streamed to the browser. The frontend
holds its own registry (frontend/src/chat/componentRegistry.tsx) and
re-validates independently; keep the two vocabularies in sync.
"""
from __future__ import annotations

# Component name -> allowed props (value = accepted type or tuple of types).
# Every prop listed is required; extra props are strictly rejected so a
# hallucinated prop can never reach the browser.
_NUMBER = (int, float)

BACKEND_COMPONENT_REGISTRY: dict[str, dict[str, object]] = {
    "signals": {"ticker": str},
    "live_price": {"ticker": str},
    "price_chart": {"ticker": str},
    "spread_payoff": {
        "ticker": str,
        "expiration": str,
        "long_strike": _NUMBER,
        "short_strike": _NUMBER,
        "kind": str,
    },
    # Curated pairs only: the card resolves the underlying from
    # arb_universe.yaml. Ad-hoc pairs (analyze_arbitrage_pair with an explicit
    # `underlying`) have no card — every prop here is required, so there is no
    # way to make `underlying` optional without weakening the validator.
    "arbitrage_pair": {"ticker": str},
    "arbitrage_spread": {"ticker": str},
    "arbitrage_premium": {"ticker": str},
    # No props: the scan covers the whole curated universe.
    "arbitrage_scan": {},
    "arbitrage_discovery": {"symbols": str},
    # No props: the card fetches the caller's own portfolio via the browser's
    # authenticated session. Never add an `owner` prop here — that would let
    # the model pick whose portfolio to show, a cross-user read (decision #20).
    "portfolio_table": {},
    # Same no-props rule as portfolio_table, for the same reason (issue #147).
    "portfolio_allocation": {},
    "symbol_lots": {"ticker": str},
    # No props: the watchlist is one global shared list (issue #83), so unlike
    # the portfolio components there is not even an owner to withhold.
    "watchlist_fundamentals": {},
    # No props: both cover the whole tracked universe (the shared watchlist plus
    # every owner's positions), so there is neither a symbol nor an owner to name.
    "fundamentals_top": {},
    "fundamentals_score_changes": {},
}


# Component name -> action name -> payload spec (same typing convention as
# prop specs). Components absent here are display-only. This is the UI->model
# backchannel vocabulary: the frontend registry declares the same actions and
# both sides validate (defense in depth, mirroring the directive registries).
BACKEND_INTERACTION_REGISTRY: dict[str, dict[str, dict[str, object]]] = {
    "spread_payoff": {
        "select_strike": {"strike": _NUMBER},
        "reprice_leg": {"leg": str, "strike": _NUMBER},
    },
    "price_chart": {
        "select_point": {"date": str, "price": _NUMBER},
    },
}


def _type_name(expected) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def _check_fields(spec: dict, values: dict, label: str, owner: str) -> tuple[bool, str]:
    """Strict field check shared by directives and interactions: every spec
    field required, extras rejected, bool never passes as a number."""
    extra = set(values) - set(spec)
    if extra:
        return False, f"Unexpected {label}s {sorted(extra)} for '{owner}'"
    for name, expected_type in spec.items():
        if name not in values:
            return False, f"Missing required {label} '{name}' for '{owner}'"
        value = values[name]
        # bool is an int subclass — never a valid stand-in for a number.
        if isinstance(value, bool) and expected_type is not bool:
            return False, (
                f"{label.capitalize()} '{name}' must be "
                f"{_type_name(expected_type)}, got bool"
            )
        if not isinstance(value, expected_type):
            return False, (
                f"{label.capitalize()} '{name}' must be {_type_name(expected_type)}, "
                f"got {type(value).__name__}"
            )
        if expected_type is str and not value.strip():
            return False, f"{label.capitalize()} '{name}' must be a non-empty string"
    return True, ""


def validate_directive(component: str, props: object) -> tuple[bool, str]:
    """Validate a show_component call. Returns (ok, reason-if-rejected)."""
    spec = BACKEND_COMPONENT_REGISTRY.get(component)
    if spec is None:
        return False, (
            f"Unknown component '{component}'. "
            f"Valid components: {sorted(BACKEND_COMPONENT_REGISTRY)}"
        )
    if not isinstance(props, dict):
        return False, f"props must be an object, got {type(props).__name__}"
    return _check_fields(spec, props, "prop", component)


def validate_interaction(interaction: object) -> tuple[bool, str]:
    """Validate one UI interaction from the request body before it is folded
    into the model conversation. Returns (ok, reason-if-rejected)."""
    if not isinstance(interaction, dict):
        return False, f"interaction must be an object, got {type(interaction).__name__}"
    component_id = interaction.get("component_id")
    if not isinstance(component_id, str) or not component_id.strip():
        return False, "interaction requires a non-empty 'component_id'"
    component = interaction.get("component", "")
    actions = BACKEND_INTERACTION_REGISTRY.get(component)
    if actions is None:
        return False, (
            f"Component '{component}' accepts no interactions. "
            f"Interactive components: {sorted(BACKEND_INTERACTION_REGISTRY)}"
        )
    action = interaction.get("action", "")
    payload_spec = actions.get(action)
    if payload_spec is None:
        return False, (
            f"Unknown action '{action}' for '{component}'. "
            f"Valid actions: {sorted(actions)}"
        )
    payload = interaction.get("payload", {})
    if not isinstance(payload, dict):
        return False, f"payload must be an object, got {type(payload).__name__}"
    ok, reason = _check_fields(payload_spec, payload, "payload field", action)
    if not ok:
        return False, reason
    props = interaction.get("props")
    if props is not None:
        ok, reason = validate_directive(component, props)
        if not ok:
            return False, f"interaction props snapshot invalid: {reason}"
    return True, ""


_SYMBOL_PROP = {
    "type": "object",
    "properties": {"symbol": {"type": "string", "description": "Ticker symbol, e.g. 'INTC'"}},
    "required": ["symbol"],
}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_stock_price",
        "description": (
            "Current stock price, Bollinger Bands (20-day, 2 std dev), and an "
            "options chain summary for a ticker symbol."
        ),
        "input_schema": _SYMBOL_PROP,
    },
    {
        "name": "get_technical_signals",
        "description": (
            "Synthesized technical signal summary for a ticker: trend, RSI, MACD, "
            "OBV, VWAP posture, and an overall interpretation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker symbol"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_rsi",
        "description": "Relative Strength Index for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "period": {"type": "integer", "description": "RSI period (default 14)"},
                "interval": {
                    "type": "string",
                    "description": "Bar interval: '1d' (default), '1wk', or '1mo'",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_macd",
        "description": "MACD (12/26/9) with crossover state for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "interval": {
                    "type": "string",
                    "description": "Bar interval: '1d' (default), '1wk', or '1mo'",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_fundamental_score",
        "description": (
            "Composite fundamental score (-14 to +14) with per-metric breakdown: "
            "revenue CAGR, margins, FCF, valuation, momentum."
        ),
        "input_schema": _SYMBOL_PROP,
    },
    {
        "name": "get_news_sentiment",
        "description": (
            "Recent news articles and aggregate FinBERT sentiment signal for a ticker."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "days": {"type": "integer", "description": "Lookback window in days (default 7)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "price_vertical_spread",
        "description": (
            "Price a two-leg vertical option spread from real contracts: returns "
            "conservative and mid debit, max profit, max loss, breakeven, "
            "risk/reward, per-leg bid/ask/IV, and liquidity warnings. A bull call "
            "spread is long the lower strike; a bear call spread is long the "
            "higher strike; use kind='put' for put spreads. After pricing, render "
            "it with show_component('spread_payoff', {same parameters})."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "expiration": {
                    "type": "string",
                    "description": "Expiration date, YYYY-MM-DD",
                },
                "long_strike": {"type": "number", "description": "Strike you buy"},
                "short_strike": {"type": "number", "description": "Strike you sell"},
                "kind": {
                    "type": "string",
                    "enum": ["call", "put"],
                    "description": "Option type for both legs (default call)",
                },
            },
            "required": ["symbol", "expiration", "long_strike", "short_strike"],
        },
    },
    {
        "name": "analyze_arbitrage_pair",
        "description": (
            "Analyse a security against the underlying it is structurally linked "
            "to (a treasury company vs its coin stack, a fund vs its reference "
            "future, a miner vs the metal it sells). Returns the gap, whether the "
            "spread mean-reverts, whether anything forces it to close, and a "
            "0-100 score with the factors that produced it.\n\n"
            "CRITICAL — read 'nav.premium_discount_pct', the NET discount. The "
            "response also carries 'nav.gross_premium_discount_pct', which "
            "ignores convertible notes and preferred stock sitting senior to the "
            "common and routinely overstates the gap by 20+ points. Quoting the "
            "gross figure as the opportunity is the specific error this tool "
            "exists to prevent. If you mention both, say plainly which is real.\n\n"
            "Scoring is inverted by design: spread width only qualifies a "
            "candidate, the convergence mechanism ranks it. Expect 'reject' — a "
            "wide gap with no forcing mechanism, negative carry and a widening "
            "trend is a directional bet, not an arbitrage. Report a rejection as "
            "the finding, not as a failure. After analysing, render it with "
            "show_component('arbitrage_pair', {'ticker': SYMBOL})."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "security": {
                    "type": "string",
                    "description": "Ticker of the listed vehicle/company, e.g. 'MSTR'",
                },
                "underlying": {
                    "type": "string",
                    "description": (
                        "Optional: underlying symbol for an ad-hoc pair not in the "
                        "curated universe, e.g. 'BTC-USD'. Omit for curated pairs."
                    ),
                },
                "days": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 3650,
                    "description": "Days of daily history (default 365)",
                },
            },
            "required": ["security"],
        },
    },
    {
        "name": "scan_arbitrage",
        "description": (
            "Rank the curated arbitrage universe, one summary row per candidate: "
            "score, verdict, NET discount, convergence mechanism, whether the "
            "hedge is executable from an equity account, and what breaks the "
            "trade. Use for 'any arbitrage opportunities?' style questions, then "
            "call analyze_arbitrage_pair for detail on anything interesting.\n\n"
            "Most scans return nothing above 'watch'. That is the scanner "
            "working, not an error — say so rather than implying the data failed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kinds": {
                    "type": "string",
                    "description": (
                        "Optional comma-separated families to include: "
                        "nav_vehicle, commodity_etf, producer. Omit for all three."
                    ),
                },
                "top_n": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Max candidates to return (default 10)",
                },
                "days": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 3650,
                    "description": "Days of daily history per pair (default 365)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_arbitrage_universe",
        "description": (
            "List the curated security-to-underlying pairs the scanner tracks, "
            "with each one's family, hedge instrument, convergence mechanism, and "
            "whether its hand-maintained holdings figures have gone stale. Use "
            "when the user asks what the scanner covers or why a symbol is absent."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "discover_arbitrage_pairs",
        "description": (
            "Sweep an ad-hoc list of symbols for statistically cointegrated "
            "links against a reference panel, gated by a sector/industry "
            "economic-link filter. Use when the user names symbols the curated "
            "universe does not cover ('is there a pair trade in MARA and "
            "RIOT?').\n\n"
            "What this does NOT produce: no NAV, no fair value, no convergence "
            "mechanism, no verdict. A discovered pair is a statistical "
            "relationship and a candidate for curation into arb_universe.yaml "
            "— never present one as a trade or imply the spread will close. "
            "list_arbitrage_universe and analyze_arbitrage_pair are the tools "
            "that carry an actual convergence claim.\n\n"
            "Reporting coverage: the result partitions every ticker you passed "
            "into `symbols_tested` and `skipped` (each skip carrying its "
            "reason — no price history, or no economic link to any reference). "
            "Read those lists rather than inferring coverage from `pairs`, and "
            "when a sweep spans several calls, reconcile the union against "
            "what the user asked for before claiming the set was covered. "
            "`count` is out of `symbols_tested`, not out of the tickers "
            "submitted.\n\n"
            "Render the result with "
            "show_component('arbitrage_discovery', {'symbols': SYMBOLS}) using "
            "the same comma-separated list you passed here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "string",
                    "description": (
                        "Comma-separated tickers to sweep, at most 25. Each one "
                        "costs a price-history fetch, so pass what the user "
                        "actually asked about rather than a broad list."
                    ),
                },
                "references": {
                    "type": "string",
                    "description": (
                        "Optional comma-separated tickers to test against, at "
                        "most 10. Omit to use the built-in reference panel."
                    ),
                },
                "days": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 3650,
                    "description": "Days of daily history per pair (default 365)",
                },
                "min_abs_correlation": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": (
                        "Correlation floor a pair must clear before the "
                        "cointegration test runs (default 0.4)"
                    ),
                },
                "require_economic_link": {
                    "type": "boolean",
                    "description": (
                        "Keep only pairs with a plausible sector/industry link "
                        "(default true). Setting this false surfaces spurious "
                        "correlations — say so if you use it."
                    ),
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "get_portfolio_summary",
        "description": (
            "The user's own holdings: portfolio-wide cost basis, current value, "
            "gain/loss in dollars and percent, and dollars-per-day, plus the "
            "same five figures per symbol. Use for 'how's my portfolio doing?', "
            "'what do I own?', or before advising on a symbol, to check whether "
            "they already hold it.\n\n"
            "Summary only — no per-lot detail. Call get_symbol_lots for one "
            "symbol's individual purchases. Pair with "
            "show_component('portfolio_table', {}) when the user wants the "
            "table, or 'portfolio_allocation' for the allocation view.\n\n"
            "Takes no parameters: it always reads the signed-in user's own "
            "portfolio, and there is no way to ask for anyone else's."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_symbol_lots",
        "description": (
            "The user's individual open purchase lots for one symbol they own — "
            "quantity, purchase price, trade date, current price, and each "
            "lot's own gain/loss and days held. Use for 'how many shares of X "
            "do I have?', 'what's my basis in X?', or when a harvest/trim "
            "question needs to know which lots exist.\n\n"
            "Returns an empty lots list if the user does not hold the symbol — "
            "that means they own none of it, not that the lookup failed. Pair "
            "with show_component('symbol_lots', {'ticker': SYMBOL}).\n\n"
            "Always reads the signed-in user's own holdings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock symbol, e.g. AAPL",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "show_component",
        "description": (
            "Render a live, data-bound UI component inline in the conversation. "
            "Use this after discussing a ticker so the user can see the real data. "
            "'signals' shows the full technical/options/risk signal panel, "
            "'live_price' shows a compact auto-refreshing price chip, "
            "'price_chart' shows the price history chart with moving averages, "
            "'spread_payoff' shows an interactive risk graph for a vertical "
            "spread (requires ticker, expiration, long_strike, short_strike, "
            "kind — the same parameters you passed to price_vertical_spread), "
            "'arbitrage_pair' shows the structural-spread card for a curated "
            "pair: net discount, the score's factor breakdown, and what breaks "
            "the trade (requires ticker; curated securities only — check "
            "list_arbitrage_universe if unsure), "
            "'arbitrage_spread' charts that pair's spread over time with its "
            "mean and standard-deviation bands, so the user can see how "
            "stretched it is rather than reading a z-score (requires ticker), "
            "'arbitrage_premium' charts a NAV vehicle's discount to net asset "
            "value over time (requires ticker; nav_vehicle securities only), "
            "'arbitrage_scan' shows the ranked scan table for the whole curated "
            "universe (no props), "
            "'arbitrage_discovery' plots a cointegration sweep — correlation "
            "against test statistic, with near-misses visible against the "
            "critical-value line (requires symbols, comma-separated), "
            "'portfolio_table' shows the caller's own portfolio as a per-symbol "
            "table with totals (no props — always the caller's own holdings, "
            "never another user's), "
            "'portfolio_allocation' charts the same holdings as a stacked bar "
            "per symbol — cost basis with the gain stacked above it or the loss "
            "cut out of it — so relative position size and which positions are "
            "carrying the portfolio are visible at a glance (no props, same "
            "caller-only rule), "
            "'symbol_lots' shows the caller's individual lots for one symbol in "
            "their portfolio (requires ticker), "
            "'watchlist_fundamentals' shows the shared watchlist ranked by "
            "fundamental score with 30-day and year-to-date returns (no props — "
            "the watchlist is one global list, not per-user), "
            "'fundamentals_top' ranks the whole tracked universe — the shared "
            "watchlist plus every owner's positions — by composite fundamental "
            "score, with how many of those symbols actually have a cached score "
            "(no props), "
            "'fundamentals_score_changes' shows which of those symbols' scores "
            "moved over the last 90 days and by how much, biggest improvements "
            "first (no props). All three portfolio "
            "components are read-only — there is no component or tool for adding, "
            "editing, or closing a lot; tell the user to use the Portfolio page "
            "for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "enum": sorted(BACKEND_COMPONENT_REGISTRY),
                    "description": "Which registered component to render",
                },
                "props": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Ticker symbol"},
                        "expiration": {
                            "type": "string",
                            "description": "spread_payoff only: expiration YYYY-MM-DD",
                        },
                        "long_strike": {
                            "type": "number",
                            "description": "spread_payoff only: strike you buy",
                        },
                        "short_strike": {
                            "type": "number",
                            "description": "spread_payoff only: strike you sell",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["call", "put"],
                            "description": "spread_payoff only: option type",
                        },
                        "symbols": {
                            "type": "string",
                            "description": (
                                "arbitrage_discovery only: comma-separated "
                                "tickers to sweep"
                            ),
                        },
                    },
                    # No schema-level required list: which props are mandatory
                    # depends on the component (arbitrage_scan takes none,
                    # arbitrage_discovery takes symbols rather than ticker), and
                    # JSON Schema cannot express that without oneOf. The
                    # per-component registry is the enforcement point and
                    # rejects both missing and extra props.
                },
            },
            "required": ["component", "props"],
        },
    },
]
