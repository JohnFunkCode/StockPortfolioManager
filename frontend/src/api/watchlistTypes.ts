/**
 * Shapes for the watchlist returns + fundamentals surface (issue #147 Part C).
 *
 * Mirrors `WatchlistFundamentalsRow` / `WatchlistFundamentalsResponse` in
 * `api/schemas/portfolio.py`. Every analytical field is optional and every one
 * means **"not known"** when absent — a symbol the fundamentals cache has never
 * scored, or one whose price history is too short for a 1y window. None of them
 * fall back to 0 on this side either: a zero score and an unscored symbol sort
 * to very different places, so the table renders an em dash rather than a
 * number it does not have.
 */

export interface WatchlistFundamentalsRow {
  symbol: string;
  name: string;
  currency: string;
  tags: string[];

  price: number | null;
  price_as_of: string | null;
  return_5d: number | null;
  return_30d: number | null;
  return_60d: number | null;
  return_ytd: number | null;
  return_1y: number | null;

  composite_score: number | null;
  fundamental_label: string | null;
  coverage: number | null;
  sector: string | null;

  /**
   * Three fields, not one. `market_cap` is the native figure quoted in
   * `market_cap_currency`; only `market_cap_usd` is comparable across
   * securities, and it is null for every non-USD name until an FX source lands
   * (issue #185). Sorting a mixed-currency column on `market_cap` is the bug
   * that put SK hynix's ₩1,456T at the top of a dollar column.
   */
  market_cap: number | null;
  market_cap_currency: string | null;
  market_cap_usd: number | null;

  rev_cagr_3y: number | null;
  rev_cagr_score: number | null;
  rev_accel_score: number | null;
  op_margin_score: number | null;
  fcf_margin_score: number | null;
  valuation_score: number | null;
  momentum_score: number | null;

  revenue_trajectory: string | null;
  earnings_date: string | null;
  eps_acceleration: string | null;

  fundamentals_cached_at: string | null;
  fundamentals_stale: boolean | null;
  fundamentals_age_hours: number | null;
}

export interface WatchlistFundamentalsResponse {
  generated_at: string;
  as_of: string | null;
  count: number;
  stale_count: number;
  unscored_count: number;
  rows: WatchlistFundamentalsRow[];
}

/** PATCH /api/watchlist/{ticker} — tags are the only editable field, and they
 * are replaced wholesale rather than merged. */
export interface UpdateWatchlistTagsResponse {
  symbol: string;
  /** As **stored**, which is not necessarily what was sent: the server strips
   * whitespace, drops blanks, and collapses duplicates. */
  tags: string[];
}
