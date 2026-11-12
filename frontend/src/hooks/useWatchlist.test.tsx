/**
 * Hook suite for the watchlist queries (issue #147 Part C). Mocks the network,
 * not the hooks — same shape as useSecurities.test.tsx.
 *
 * The invalidation cases matter more than usual here: the page and the
 * securities table render the same tags off two different cache keys, so a
 * mutation that refreshes only one of them shows two answers on two tabs.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';

import {
  useRefreshWatchlistQuotes,
  useSetWatchlistTags,
  useWatchlistFundamentals,
  useWatchlistQuotes,
} from './useWatchlist';
import { useAddSecurity, useRemoveFromWatchlist } from './useSecurities';
import {
  makeQueryClient,
  mockApi,
  renderHookWithProviders,
  quoteRow,
  watchlistFundamentals,
  watchlistQuotes,
  watchlistRow,
} from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A client that records which query keys got invalidated. */
function spyingClient(): { qc: QueryClient; invalidated: string[] } {
  const qc = makeQueryClient();
  const invalidated: string[] = [];
  const original = qc.invalidateQueries.bind(qc);
  vi.spyOn(qc, 'invalidateQueries').mockImplementation((filters) => {
    const key = (filters as { queryKey?: unknown[] } | undefined)?.queryKey;
    if (Array.isArray(key)) invalidated.push(String(key[0]));
    return original(filters);
  });
  return { qc, invalidated };
}

/** A client with the app's default of one retry (`main.tsx`), made instant. */
function appLikeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: 1, retryDelay: 0, gcTime: 0, staleTime: 0 } },
  });
}

describe('useWatchlistFundamentals', () => {
  it('fetches the whole table in one request', async () => {
    const api = mockApi([
      [
        '/api/watchlist/fundamentals',
        watchlistFundamentals([watchlistRow('INTC'), watchlistRow('WMT')]),
      ],
    ]);
    const { result } = renderHookWithProviders(() => useWatchlistFundamentals());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.rows).toHaveLength(2);
    expect(api.calls).toHaveLength(1);
    expect(api.unmatched).toEqual([]);
  });

  it('surfaces a failure rather than an empty table', async () => {
    mockApi([['/api/watchlist/fundamentals', { __status: 500, error: 'watchlist down' }]]);
    const { result } = renderHookWithProviders(() => useWatchlistFundamentals());
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toMatch(/watchlist down/i);
  });
});

describe('useSetWatchlistTags', () => {
  it('PATCHes the ticker and returns the stored tags', async () => {
    const api = mockApi([['/api/watchlist/INTC', { symbol: 'INTC', tags: ['chips'] }]]);
    const { result } = renderHookWithProviders(() => useSetWatchlistTags());
    result.current.mutate({ ticker: 'INTC', tags: ['chips'] });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(api.calls[0][1]?.method).toBe('PATCH');
    expect(result.current.data!.tags).toEqual(['chips']);
  });

  it('refreshes both the watchlist page and the securities table', async () => {
    mockApi([['/api/watchlist/INTC', { symbol: 'INTC', tags: [] }]]);
    const { qc, invalidated } = spyingClient();
    const { result } = renderHookWithProviders(() => useSetWatchlistTags(), { queryClient: qc });
    result.current.mutate({ ticker: 'INTC', tags: [] });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidated).toContain('watchlist-fundamentals');
    expect(invalidated).toContain('securities');
  });

  it('exposes the error instead of silently keeping the old tags', async () => {
    mockApi([['/api/watchlist/NOPE', { __status: 404, error: 'NOPE not found in watchlist' }]]);
    const { result } = renderHookWithProviders(() => useSetWatchlistTags());
    result.current.mutate({ ticker: 'NOPE', tags: ['x'] });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('shared mutations reach this page too', () => {
  // Both live in useSecurities.ts because the Add Security dialog writes to
  // either list — so their invalidation has to cover the watchlist key, or the
  // page the user just acted on is the one that keeps showing the stale row.
  it('useRemoveFromWatchlist invalidates the watchlist key as well', async () => {
    mockApi([['/api/watchlist/INTC', { symbol: 'INTC', removed: true }]]);
    const { qc, invalidated } = spyingClient();
    const { result } = renderHookWithProviders(() => useRemoveFromWatchlist(), { queryClient: qc });
    result.current.mutate('INTC');
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidated).toContain('watchlist-fundamentals');
    expect(invalidated).toContain('securities');
  });

  it('useAddSecurity invalidates the watchlist key as well', async () => {
    mockApi([['/api/watchlist', { added: true }]]);
    const { qc, invalidated } = spyingClient();
    const { result } = renderHookWithProviders(() => useAddSecurity(), { queryClient: qc });
    result.current.watchlist.mutate({ symbol: 'INTC' } as never);
    await waitFor(() => expect(result.current.watchlist.isSuccess).toBe(true));

    expect(invalidated).toContain('watchlist-fundamentals');
    expect(invalidated).toContain('securities');
  });
});


describe('useWatchlistQuotes', () => {
  it('reads the quotes without force', async () => {
    const api = mockApi([['/api/watchlist/quotes', watchlistQuotes([quoteRow('INTC')])]]);
    const { result } = renderHookWithProviders(() => useWatchlistQuotes());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.quotes[0].symbol).toBe('INTC');
    expect(api.calls[0][0]).not.toContain('force');
  });

  it('does not poll', async () => {
    mockApi([['/api/watchlist/quotes', watchlistQuotes()]]);
    const qc = makeQueryClient();
    const { result } = renderHookWithProviders(() => useWatchlistQuotes(), { queryClient: qc });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const [query] = qc.getQueryCache().findAll({ queryKey: ['watchlist-quotes'] });
    const options = query.observers[0].options;
    expect(options.refetchInterval).toBeFalsy();
    expect(options.staleTime).toBe(60 * 1000);
  });

  it('does not retry a failed request, even when the app default retries', async () => {
    const api = mockApi([['/api/watchlist/quotes', { __status: 500, error: 'quotes down' }]]);
    const qc = appLikeClient();
    const { result } = renderHookWithProviders(() => useWatchlistQuotes(), { queryClient: qc });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(api.calls).toHaveLength(1);
  });
});

describe('useRefreshWatchlistQuotes', () => {
  it('forces a refetch into the cache entry the page reads', async () => {
    let served = 0;
    const api = mockApi([
      ['/api/watchlist/quotes', () =>
        watchlistQuotes([quoteRow('INTC', { return_1d_display: ++served })])],
    ]);
    const qc = makeQueryClient();
    const { result } = renderHookWithProviders(
      () => ({ quotes: useWatchlistQuotes(), refresh: useRefreshWatchlistQuotes() }),
      { queryClient: qc },
    );
    await waitFor(() => expect(result.current.quotes.data?.quotes[0].return_1d_display).toBe(1));

    await result.current.refresh();

    await waitFor(() => expect(result.current.quotes.data?.quotes[0].return_1d_display).toBe(2));
    expect(api.calls[1][0]).toContain('force=true');
  });

  it('does not retry a failed refresh, even when the app default retries', async () => {
    let fail = false;
    const api = mockApi([
      ['/api/watchlist/quotes', () =>
        fail ? { __status: 500, error: 'quotes down' } : watchlistQuotes([quoteRow('INTC')])],
    ]);
    const qc = appLikeClient();
    const { result } = renderHookWithProviders(
      () => ({ quotes: useWatchlistQuotes(), refresh: useRefreshWatchlistQuotes() }),
      { queryClient: qc },
    );
    await waitFor(() => expect(result.current.quotes.isSuccess).toBe(true));

    fail = true;
    await expect(result.current.refresh()).rejects.toThrow();
    expect(api.calls).toHaveLength(2);
  });
});
