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

/** The merged `/watchlist` page's read (issue #224): the fundamentals rows
 * with the caller's own holdings marked. */
export function useWatchlistMine() {
  return useQuery({
    queryKey: ['watchlist-mine'],
    queryFn: () => watchlistApi.getMine(),
    staleTime: 5 * 60 * 1000,
  });
}

/** Replace a symbol's tags.
 *
 * Invalidates every read that renders the tags (`['watchlist-mine']`,
 * `['watchlist-fundamentals']`, `['securities']`), so no two views disagree. */
export function useSetWatchlistTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ticker, tags }: { ticker: string; tags: string[] }) =>
      watchlistApi.setTags(ticker, tags),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watchlist-mine'] });
      qc.invalidateQueries({ queryKey: ['watchlist-fundamentals'] });
      qc.invalidateQueries({ queryKey: ['securities'] });
    },
  });
}
