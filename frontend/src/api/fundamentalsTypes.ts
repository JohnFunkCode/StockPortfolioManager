/**
 * Shapes for the fundamentals collection surface (issue #147 Part D).
 *
 * These mirror the dicts `quantcore/services/fundamentals.py` returns from
 * `get_top_fundamental_stocks`, `get_sector_fundamental_breakdown`,
 * `get_fundamental_score_changes`, `get_upcoming_earnings` and
 * `get_cache_stats`. The routes are pass-throughs with no Pydantic response
 * model, so these interfaces are the only written-down contract on this side —
 * keep them in step with the service when a field moves.
 *
 * Nullable analytical fields mean **"not known"**, never zero: a symbol the
 * nightly warmer has not reached yet has no composite score, and rendering that
 * as 0 would sort it against companies that genuinely score badly.
 */

/**
 * Which slice of the cache a collection read covers (issue #147 Part B4).
 *
 * `all` is every symbol anyone has ever asked about — the cache accumulates
 * one-off lookups from the sidekick and the MCP tools. `tracked` narrows to the
 * shared watchlist plus every owner's positions, which is the universe the
 * nightly warmer actually refreshes and therefore the only one whose staleness
 * numbers mean anything. The page reads `tracked`; the default stays `all` for
 * existing callers.
 */
export type FundamentalsScope = 'all' | 'tracked';

// --------------------------------------------------------------------------- //
// GET /api/securities/fundamentals/top
// --------------------------------------------------------------------------- //

export interface FundamentalRanking {
  rank: number;
  symbol: string;
  composite_score: number | null;
  fundamental_label: string | null;
  coverage: number | null;
  /** When the warmer last scored this symbol. */
  cached_at: string | null;
}

export interface TopFundamentalsResponse {
  ranked_at: string;
  n_requested: number;
  scope: FundamentalsScope;
  /** Size of the scoped set, not of the whole cache, when scope is `tracked`. */
  total_in_cache: number;
  /** How many of those cleared `min_coverage` and have a score at all. The gap
   * between this and `total_in_cache` is the warming backlog. */
  eligible_count: number;
  min_coverage: number;
  rankings: FundamentalRanking[];
}

// --------------------------------------------------------------------------- //
// GET /api/securities/fundamentals/sector-breakdown
// --------------------------------------------------------------------------- //

export interface SectorRanking {
  rank: number;
  symbol: string;
  composite_score: number | null;
  fundamental_label: string | null;
  coverage: number | null;
}

export interface SectorBreakdownResponse {
  queried_at: string;
  sector_filter: string | null;
  scope: FundamentalsScope;
  top_n: number;
  /** Sector name → its best names, already ranked and truncated to `top_n`.
   * Symbols with no sector on file land under `"Unknown"`. */
  sectors: Record<string, SectorRanking[]>;
  sector_count: number;
  total_symbols: number;
}

// --------------------------------------------------------------------------- //
// GET /api/securities/fundamentals/score-changes
// --------------------------------------------------------------------------- //

export type ScoreChangeDirection = 'improving' | 'deteriorating';

export interface FundamentalScoreChange {
  symbol: string;
  /** Signed: positive is improving. */
  delta: number;
  direction: ScoreChangeDirection;
  score_then: number;
  score_now: number;
  label_then: string | null;
  label_now: string | null;
  first_snapshot: string | null;
  last_snapshot: string | null;
}

export interface ScoreChangesResponse {
  queried_at: string;
  since_days: number;
  min_delta: number;
  direction: ScoreChangeDirection | 'both';
  scope: FundamentalsScope;
  symbols_evaluated: number;
  /** Fewer than two snapshots in the window — a symbol the warmer only reached
   * once. Not a mover, and not evidence of stability either. */
  symbols_with_insufficient_history: number;
  changes: FundamentalScoreChange[];
}

// --------------------------------------------------------------------------- //
// GET /api/securities/fundamentals/upcoming-earnings
// --------------------------------------------------------------------------- //

export interface UpcomingEarning {
  symbol: string;
  /** Date only (`YYYY-MM-DD`); the cache holds no time of day. */
  earnings_date: string;
  days_to_earnings: number;
  risk_level: string | null;
  pre_earnings_setup: boolean;
  historical_avg_move_pct: number | null;
  cached_at: string | null;
  stale: boolean;
}

export interface UpcomingEarningsResponse {
  queried_at: string;
  days_window: number;
  scope: FundamentalsScope;
  include_stale: boolean;
  /** Dropped for being past their TTL, not for being outside the window. A
   * large number here means the calendar is under-reporting. */
  stale_excluded: number;
  total_in_cache: number;
  count: number;
  upcoming: UpcomingEarning[];
}

// --------------------------------------------------------------------------- //
// GET /api/securities/fundamentals/cache-stats
// --------------------------------------------------------------------------- //

export interface CacheDataTypeStats {
  data_type: string;
  symbol_count: number;
  oldest: string | null;
  newest: string | null;
}

export interface FundamentalsCacheStats {
  /** `host:port/name` from `quantcore.db.describe_dsn()` — never the DSN, which
   * carries the password. */
  database?: string;
  data_types: CacheDataTypeStats[];
  error?: string;
}
