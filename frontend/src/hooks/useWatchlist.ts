import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { watchlistApi } from '../api/watchlist';

/** The `/watchlist` page's single read (issue #147 Part C).
 *
 * `staleTime` matches `useSecurities`: the underlying fundamentals are a
 * nightly cache, so a five-minute window costs nothing and keeps tab switches
 * instant. */
export function useWatchlistFundamentals() {
  return useQuery({
    queryKey: ['watchlist-fundamentals'],
    queryFn: () => watchlistApi.getFundamentals(),
    staleTime: 5 * 60 * 1000,
  });
}

/** Replace a symbol's tags.
 *
 * Invalidates `['securities']` as well as this page's own key: the securities
 * table renders the same tags and drives its filter off them, so leaving it
 * alone would show two different answers on two tabs. */
export function useSetWatchlistTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ticker, tags }: { ticker: string; tags: string[] }) =>
      watchlistApi.setTags(ticker, tags),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watchlist-fundamentals'] });
      qc.invalidateQueries({ queryKey: ['securities'] });
    },
  });
}
