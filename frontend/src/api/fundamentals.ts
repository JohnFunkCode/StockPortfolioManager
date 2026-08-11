/**
 * Fundamentals client — the five collection reads behind the `/fundamentals`
 * page (issue #147 Part D), which replaces the nightly
 * `generate_watchlist_fundamentals_report.py` HTML.
 *
 * Every call here is cache-backed and does no network IO server-side: the
 * nightly warmer fills `fundamentals_history`, and these routes read it. That
 * is what makes five parallel requests reasonable rather than five minutes of
 * yfinance.
 *
 * All five default to `scope: 'tracked'`. The page is about the securities the
 * team actually follows, and the cache also holds every symbol anyone ever
 * asked the sidekick about — ranking the cache would put a one-off lookup at
 * the top of a page titled after the watchlist. `scope` stays a parameter
 * rather than a constant so a caller can still ask for the whole cache.
 *
 * Per-symbol fundamentals (score, revenue growth, history) live in
 * `securities.ts` with the rest of the security-detail surface; only the
 * collection-level reads are here.
 */
import { apiRequest } from './client';
import type {
  FundamentalsScope,
  TopFundamentalsResponse,
  SectorBreakdownResponse,
  ScoreChangesResponse,
  UpcomingEarningsResponse,
  FundamentalsCacheStats,
  ScoreChangeDirection,
} from './fundamentalsTypes';

export type {
  FundamentalsScope,
  TopFundamentalsResponse,
  SectorBreakdownResponse,
  ScoreChangesResponse,
  UpcomingEarningsResponse,
  FundamentalsCacheStats,
  ScoreChangeDirection,
} from './fundamentalsTypes';
export type {
  FundamentalRanking,
  SectorRanking,
  FundamentalScoreChange,
  UpcomingEarning,
  CacheDataTypeStats,
} from './fundamentalsTypes';

const BASE = '/api/securities/fundamentals';

export const fundamentalsApi = {
  /** Ranked by composite score, best first. `min_coverage` drops symbols the
   * warmer has only partially filled — a score computed from two of seven
   * inputs is not comparable to one computed from all seven. */
  getTop: (
    { n = 25, minCoverage = 0.5, scope = 'tracked' as FundamentalsScope } = {},
  ) =>
    apiRequest<TopFundamentalsResponse>(
      `${BASE}/top?n=${n}&min_coverage=${minCoverage}&scope=${scope}`,
    ),

  /** The same ranking cut by sector, `top_n` names each. */
  getSectorBreakdown: (
    { topN = 5, scope = 'tracked' as FundamentalsScope } = {},
  ) =>
    apiRequest<SectorBreakdownResponse>(
      `${BASE}/sector-breakdown?top_n=${topN}&scope=${scope}`,
    ),

  /** Score movement over `sinceDays`, first snapshot vs last. Needs two
   * snapshots in the window, so a newly tracked symbol never appears. */
  getScoreChanges: (
    {
      minDelta = 2,
      sinceDays = 90,
      direction = 'both' as ScoreChangeDirection | 'both',
      scope = 'tracked' as FundamentalsScope,
    } = {},
  ) =>
    apiRequest<ScoreChangesResponse>(
      `${BASE}/score-changes?min_delta=${minDelta}&since_days=${sinceDays}` +
        `&direction=${direction}&scope=${scope}`,
    ),

  /** Earnings inside the next `days`. Stale cache rows are excluded by default:
   * a date the warmer read weeks ago may since have moved, and a wrong date is
   * worse than a missing one. The response reports how many it dropped. */
  getUpcomingEarnings: (
    {
      days = 14,
      includeStale = false,
      scope = 'tracked' as FundamentalsScope,
    } = {},
  ) =>
    apiRequest<UpcomingEarningsResponse>(
      `${BASE}/upcoming-earnings?days=${days}&include_stale=${includeStale}` +
        `&scope=${scope}`,
    ),

  /** Cache inventory per data_type — what the freshness banner reads. Takes no
   * scope: it reports on the cache itself, not on a roster. */
  getCacheStats: () => apiRequest<FundamentalsCacheStats>(`${BASE}/cache-stats`),
};
