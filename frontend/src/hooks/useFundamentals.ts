/**
 * The `/fundamentals` page's five reads (issue #147 Part D).
 *
 * Five hooks rather than one composite call, because react-query runs them in
 * parallel and each panel then renders as soon as its own answer lands — the
 * page has no ordering between them. They are all cache-backed reads with no
 * server-side network IO, so the fan-out is cheap.
 *
 * `staleTime` is five minutes everywhere, matching `useWatchlistFundamentals`
 * and `useSecurities`: the data underneath is a nightly warm, so a shorter
 * window would only re-fetch numbers that cannot have changed.
 */
import { useQuery } from '@tanstack/react-query';
import { fundamentalsApi } from '../api/fundamentals';
import type { FundamentalsScope, ScoreChangeDirection } from '../api/fundamentalsTypes';

const STALE_TIME = 5 * 60 * 1000;

/** Query keys carry every parameter that changes the answer — `scope` included,
 * or switching scopes would serve the previous roster's ranking from cache. */
export function useTopFundamentals(
  { n = 25, minCoverage = 0.5, scope = 'tracked' as FundamentalsScope } = {},
) {
  return useQuery({
    queryKey: ['fundamentals', 'top', scope, n, minCoverage],
    queryFn: () => fundamentalsApi.getTop({ n, minCoverage, scope }),
    staleTime: STALE_TIME,
  });
}

export function useSectorBreakdown(
  { topN = 5, scope = 'tracked' as FundamentalsScope } = {},
) {
  return useQuery({
    queryKey: ['fundamentals', 'sector-breakdown', scope, topN],
    queryFn: () => fundamentalsApi.getSectorBreakdown({ topN, scope }),
    staleTime: STALE_TIME,
  });
}

export function useScoreChanges(
  {
    minDelta = 2,
    sinceDays = 90,
    direction = 'both' as ScoreChangeDirection | 'both',
    scope = 'tracked' as FundamentalsScope,
  } = {},
) {
  return useQuery({
    queryKey: ['fundamentals', 'score-changes', scope, minDelta, sinceDays, direction],
    queryFn: () =>
      fundamentalsApi.getScoreChanges({ minDelta, sinceDays, direction, scope }),
    staleTime: STALE_TIME,
  });
}

export function useUpcomingEarnings(
  {
    days = 14,
    includeStale = false,
    scope = 'tracked' as FundamentalsScope,
  } = {},
) {
  return useQuery({
    queryKey: ['fundamentals', 'upcoming-earnings', scope, days, includeStale],
    queryFn: () =>
      fundamentalsApi.getUpcomingEarnings({ days, includeStale, scope }),
    staleTime: STALE_TIME,
  });
}

export function useFundamentalsCacheStats() {
  return useQuery({
    queryKey: ['fundamentals', 'cache-stats'],
    queryFn: () => fundamentalsApi.getCacheStats(),
    staleTime: STALE_TIME,
  });
}
