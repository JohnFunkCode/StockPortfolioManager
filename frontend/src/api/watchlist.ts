/**
 * Watchlist client — the returns + fundamentals table behind the `/watchlist`
 * page (issue #147 Part C), plus the tag edit.
 *
 * Add and remove deliberately live in `securities.ts` alongside their portfolio
 * twins: the Add Security dialog writes to either list, and a symbol that is in
 * both lists must invalidate the same `['securities']` cache. Only the reads
 * and the write that has no portfolio counterpart are here.
 */
import { apiRequest } from './client';
import type {
  WatchlistFundamentalsResponse,
  UpdateWatchlistTagsResponse,
} from './watchlistTypes';

export type { WatchlistFundamentalsResponse, UpdateWatchlistTagsResponse };
export type { WatchlistFundamentalsRow } from './watchlistTypes';

export const watchlistApi = {
  /** The whole list with returns and cached fundamentals. Served in six
   * queries and zero network calls server-side, so it is one request rather
   * than one per symbol — do not turn this into a per-row fetch. */
  getFundamentals: () =>
    apiRequest<WatchlistFundamentalsResponse>('/api/watchlist/fundamentals'),

  /** Replace `ticker`'s tags. The full chip set the page is displaying, not a
   * delta — the server replaces rather than merges, which is the only way a
   * tag can be removed. */
  setTags: (ticker: string, tags: string[]) =>
    apiRequest<UpdateWatchlistTagsResponse>(
      `/api/watchlist/${encodeURIComponent(ticker)}`,
      { method: 'PATCH', body: JSON.stringify({ tags }) },
    ),
};
