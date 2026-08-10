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

import { useSetWatchlistTags, useWatchlistFundamentals } from './useWatchlist';
import { useAddSecurity, useRemoveFromWatchlist } from './useSecurities';
import {
  makeQueryClient,
  mockApi,
  renderHookWithProviders,
  watchlistFundamentals,
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
