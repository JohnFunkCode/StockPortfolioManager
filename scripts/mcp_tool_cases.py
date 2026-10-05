"""Every MCP tool's arguments and expected REST call (issue #44).

The single home for the per-tool cases. Two consumers:

- ``tests/test_mcp_tool_contracts.py`` calls each tool offline against a
  stubbed REST seam and checks the request matches the expected call;
- ``scripts/mcp_live_smoke.py`` calls the read-only tools against the deployed
  **test** wrappers, with the first variant's arguments.

A new tool needs a case here; the contract test's completeness guard fails
without one.
"""

SP = "fastMCPTest.stock_price_server"
OA = "fastMCPTest.options_analysis"
CF = "fastMCPTest.company_fundamentals_server"
NS = "fastMCPTest.news_sentiment_server"
MA = "fastMCPTest.market_analysis_server"
PF = "fastMCPTest.portfolio_server"
AR = "fastMCPTest.arbitrage_server"

SEC = "/api/securities/BRK-B"


def call(method, path, query=None, body=None):
    """The REST call a tool is expected to make. Query values are as sent
    on the wire (strings); a list value is a repeated key."""
    return {"method": method, "path": path, "query": query or {}, "body": body}


def G(path, **query):
    return call("GET", path, query)


# (module, tool) -> list of (args, expected call or None for "no REST call").
# The first variant of each tool uses only required arguments, which pins the
# defaults; later variants exercise arguments that are sent conditionally.
CASES = {
    # ---- stock-price (21) ------------------------------------------------
    (SP, "get_news"): [({"symbol": "BRK-B"}, G(f"{SEC}/news", max_articles="10"))],
    (SP, "get_stock_price"): [({"symbol": "BRK-B"}, G(f"{SEC}/price-summary"))],
    (SP, "get_rsi"): [({"symbol": "BRK-B"}, G(f"{SEC}/rsi", period="14", interval="1d"))],
    (SP, "get_macd"): [({"symbol": "BRK-B"}, G(f"{SEC}/macd", interval="1d"))],
    (SP, "get_stochastic"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/stochastic", k_period="14", d_period="3", interval="1d"))],
    (SP, "get_volume_analysis"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/volume", lookback="20", interval="1d"))],
    (SP, "get_obv"): [({"symbol": "BRK-B"}, G(f"{SEC}/obv", lookback="20", interval="1d"))],
    (SP, "get_vwap"): [({"symbol": "BRK-B"}, G(f"{SEC}/vwap", lookback="20", interval="1d"))],
    (SP, "get_candlestick_patterns"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/candlestick", lookback="10", interval="1d"))],
    (SP, "get_higher_lows"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/higher-lows", swing_bars="3", lookback_swings="6", interval="1h"))],
    (SP, "get_gap_analysis"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/gaps", min_gap_pct="0.5", lookback="60", interval="1d"))],
    (SP, "get_atr_bands"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/atr-bands", period="14", band_mult="2.0", stop_mult="3.0",
        interval="1d", lookback="250"))],
    (SP, "get_anchored_vwap"): [
        ({"symbol": "BRK-B"}, G(f"{SEC}/anchored-vwap", lookback_days="365")),
        ({"symbol": "BRK-B", "anchor_date": "2026-01-02"}, G(
            f"{SEC}/anchored-vwap", anchor_date="2026-01-02", lookback_days="365")),
    ],
    (SP, "get_volume_profile"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/volume-profile", days="365", interval="1d", bins="50",
        value_area_pct="0.7"))],
    (SP, "get_support_confluence"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/support-confluence", tolerance_pct="1.0", max_expirations="4",
        max_zones="5"))],
    (SP, "get_historical_drawdown"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/drawdown", lookback_days="252"))],
    (SP, "get_stop_loss_analysis"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/stop-loss", cost_basis="0.0", shares="0", max_expirations="4"))],
    (SP, "get_vwap_history"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/vwap/history", since_days="90", lookback="20", interval="1d"))],
    (SP, "get_relative_strength_history"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/relative-strength/history", since_days="90", rs_period="21",
        interval="1d"))],
    (SP, "get_relative_strength"): [({"symbol": "BRK-B"}, G(f"{SEC}/relative-strength"))],
    (SP, "get_trade_recommendation"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/recommendation", capital="5000.0"))],

    # ---- options-analysis (11) -------------------------------------------
    (OA, "mcp_health_check"): [({}, None)],
    # watchlist_path is deliberately not forwarded: the REST tier reads the DB.
    (OA, "analyze_options_watchlist"): [
        ({}, G("/api/options/screen-watchlist", puts_budget="1000.0", top_n="10",
               include_non_us="false")),
        ({"watchlist_path": "/etc/passwd"}, G(
            "/api/options/screen-watchlist", puts_budget="1000.0", top_n="10",
            include_non_us="false")),
    ],
    (OA, "analyze_options_symbol"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/screen", puts_budget="1000.0", top_n="10"))],
    # max_snapshot_age_minutes / allow_live_fetch are deliberately not
    # forwarded: the route takes only expirations, strikes and kind.
    (OA, "get_option_contracts"): [
        ({"symbol": "BRK-B", "expirations": ["2026-11-20", "2026-12-18"],
          "strikes": [400.0, 410.5], "max_snapshot_age_minutes": 5,
          "allow_live_fetch": False},
         G(f"{SEC}/options/contracts", expirations=["2026-11-20", "2026-12-18"],
           strikes=["400.0", "410.5"], kind="call")),
    ],
    (OA, "price_vertical_spread"): [
        ({"symbol": "BRK-B", "expiration": "2026-11-20", "long_strike": 400.0,
          "short_strike": 410.0},
         call("POST", f"{SEC}/options/vertical-spread", body={
             "expiration": "2026-11-20", "long_strike": 400.0, "short_strike": 410.0,
             "kind": "call", "max_snapshot_age_minutes": 15, "allow_live_fetch": True})),
    ],
    (OA, "get_full_options_chain"): [({"symbol": "BRK-B"}, G(f"{SEC}/options/full-chain"))],
    (OA, "get_unusual_calls"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/unusual-calls", min_volume="100", min_vol_oi_ratio="0.5",
        max_expirations="3"))],
    (OA, "get_delta_adjusted_oi"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/delta-adjusted-oi", max_expirations="3", risk_free_rate="0.045"))],
    (OA, "get_gamma_wall_history"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/gamma-wall-history", since_days="90"))],
    (OA, "get_oi_change_analysis"): [
        ({"symbol": "BRK-B"}, G(f"{SEC}/options/oi-change", days="30", top_n="10",
                                min_oi="100")),
        ({"symbol": "BRK-B", "expiration": "2026-11-20"}, G(
            f"{SEC}/options/oi-change", days="30", top_n="10", min_oi="100",
            expiration="2026-11-20")),
    ],
    (OA, "get_gex_profile"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/gex-profile", max_expirations="6", risk_free_rate="0.045"))],

    # ---- company-fundamentals (12) ---------------------------------------
    (CF, "get_earnings_calendar"): [({"symbol": "BRK-B"}, G(f"{SEC}/earnings-calendar"))],
    (CF, "get_fundamental_score"): [({"symbol": "BRK-B"}, G(f"{SEC}/fundamentals/score"))],
    (CF, "get_revenue_growth"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/fundamentals/revenue-growth"))],
    (CF, "get_earnings_acceleration"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/fundamentals/earnings-acceleration"))],
    (CF, "get_fundamental_scores_batch"): [
        ({"symbols": ["aapl", " MSFT ", "AAPL"]},
         call("POST", "/api/securities/fundamentals/scores-batch",
              body={"symbols": ["AAPL", "MSFT"]})),
    ],
    (CF, "get_full_fundamental_profile"): [({"symbol": "BRK-B"}, G(f"{SEC}/fundamentals"))],
    (CF, "get_top_fundamental_stocks"): [({}, G(
        "/api/securities/fundamentals/top", n="10", min_coverage="0.5"))],
    (CF, "get_upcoming_earnings"): [({}, G(
        "/api/securities/fundamentals/upcoming-earnings", days="14",
        include_stale="false"))],
    (CF, "get_cache_stats"): [({}, G("/api/securities/fundamentals/cache-stats"))],
    (CF, "get_sector_fundamental_breakdown"): [
        ({}, G("/api/securities/fundamentals/sector-breakdown", top_n="5")),
        ({"sector": "Technology"}, G("/api/securities/fundamentals/sector-breakdown",
                                     sector="Technology", top_n="5")),
    ],
    (CF, "get_fundamental_score_changes"): [({}, G(
        "/api/securities/fundamentals/score-changes", min_delta="2", since_days="90",
        direction="both"))],
    (CF, "get_fundamental_history"): [({"symbol": "BRK-B", "data_type": "score"}, G(
        f"{SEC}/fundamentals/history", data_type="score", since_days="365"))],

    # ---- news-sentiment (4) ----------------------------------------------
    (NS, "collect_news"): [({"symbol": "BRK-B"}, call(
        "POST", f"{SEC}/news/collect", {"score": "true"}))],
    (NS, "get_news_sentiment"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/news/sentiment", days="7", scored_only="false"))],
    (NS, "get_sentiment_trend"): [({"symbol": "BRK-B"}, G(f"{SEC}/news/trend", days="30"))],
    (NS, "list_news_symbols"): [({}, G("/api/securities/news/symbols"))],

    # ---- market-analysis (3) ---------------------------------------------
    (MA, "get_short_interest"): [({"symbol": "BRK-B"}, G(f"{SEC}/short-interest"))],
    (MA, "get_dark_pool"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/dark-pool", lookback="20", interval="1d"))],
    (MA, "get_bid_ask_spread"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/bid-ask-spread", lookback="20"))],

    # ---- portfolio (6) ---------------------------------------------------
    (PF, "mcp_health_check"): [({}, None)],
    (PF, "get_portfolio"): [({}, G("/api/portfolio/symbols"))],
    (PF, "get_symbol_lots"): [({"ticker": " brk-b "}, G("/api/portfolio/lots"))],
    (PF, "get_portfolio_summary"): [({}, G("/api/portfolio/symbols"))],
    (PF, "list_watchlist"): [({}, G("/api/watchlist"))],
    (PF, "add_to_watchlist"): [
        ({"symbol": "BRK-B"}, call("POST", "/api/watchlist",
                                   body={"symbol": "BRK-B", "name": None, "tags": None})),
        ({"symbol": "BRK-B", "name": "Berkshire", "tags": ["value"]},
         call("POST", "/api/watchlist",
              body={"symbol": "BRK-B", "name": "Berkshire", "tags": ["value"]})),
    ],

    # ---- arbitrage (5) ---------------------------------------------------
    (AR, "mcp_health_check"): [({}, None)],
    (AR, "list_arbitrage_universe"): [({}, G("/api/arbitrage/universe"))],
    (AR, "analyze_arbitrage_pair"): [
        ({"security": "PSLV"}, G("/api/arbitrage/pairs/PSLV", days="365")),
        ({"security": "PSLV", "underlying": "SLV", "zscore_window": 60}, G(
            "/api/arbitrage/pairs/PSLV", underlying="SLV", days="365",
            zscore_window="60")),
    ],
    (AR, "scan_arbitrage"): [
        ({}, G("/api/arbitrage/scan", top_n="20", days="365")),
        ({"kinds": "nav_vehicle"}, G("/api/arbitrage/scan", kinds="nav_vehicle",
                                     top_n="20", days="365")),
    ],
    (AR, "discover_arbitrage_pairs"): [
        ({"symbols": "GLD,GDX"}, G(
            "/api/arbitrage/discover", symbols="GLD,GDX", days="365",
            min_abs_correlation="0.4", require_economic_link="true")),
        ({"symbols": "GLD,GDX", "references": "SPY"}, G(
            "/api/arbitrage/discover", symbols="GLD,GDX", references="SPY", days="365",
            min_abs_correlation="0.4", require_economic_link="true")),
    ],
}
