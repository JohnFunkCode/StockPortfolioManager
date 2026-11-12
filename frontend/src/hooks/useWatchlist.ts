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

const WATCHLIST_QUOTES_KEY = ['watchlist-quotes'];

/** Live 1d columns for the watchlist (issue #518), merged onto the
 * `useWatchlistMine` rows by symbol.
 *
 * Fetched on load and on the Refresh button only: `staleTime` matches the
 * server's 60 s quote cache and there is deliberately **no**
 * `refetchInterval`. Polling 260 symbols from every open tab is the load the
 * batch endpoint was built to avoid. */
export function useWatchlistQuotes() {
  return useQuery({
    queryKey: WATCHLIST_QUOTES_KEY,
    queryFn: () => watchlistApi.getQuotes(),
    staleTime: 60 * 1000,
  });
}

/** The Refresh button: refetch the quotes with `force=true`, into the same
 * cache entry `useWatchlistQuotes` reads. */
export function useRefreshWatchlistQuotes() {
  const qc = useQueryClient();
  return () =>
    qc.fetchQuery({
      queryKey: WATCHLIST_QUOTES_KEY,
      queryFn: () => watchlistApi.getQuotes(true),
      staleTime: 0,
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
