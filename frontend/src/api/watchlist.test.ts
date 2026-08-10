/**
 * Endpoint-shape tests for watchlistApi (issue #147 Part C): URL, method,
 * encoding, and body. The hook and page suites cover behaviour; this covers
 * the wire.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { watchlistApi } from './watchlist';
import { mockApi, watchlistFundamentals } from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('watchlistApi', () => {
  it('getFundamentals issues exactly one GET for the whole list', async () => {
    const api = mockApi([['/api/watchlist/fundamentals', watchlistFundamentals()]]);
    const data = await watchlistApi.getFundamentals();

    // One request, not one per symbol — the server serves this in six queries
    // and a per-row fetch would undo that.
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0][0]).toContain('/api/watchlist/fundamentals');
    expect(api.calls[0][1]?.method ?? 'GET').toBe('GET');
    expect(data.rows[0].symbol).toBe('INTC');
  });

  it('setTags PATCHes the full tag set to the per-symbol route', async () => {
    const api = mockApi([['/api/watchlist/INTC', { symbol: 'INTC', tags: ['chips', 'ai'] }]]);
    const data = await watchlistApi.setTags('INTC', ['chips', 'ai']);

    expect(api.calls[0][0]).toContain('/api/watchlist/INTC');
    expect(api.calls[0][1]?.method).toBe('PATCH');
    // The whole set, not a delta — the server replaces rather than merges.
    expect(JSON.parse(String(api.calls[0][1]?.body))).toEqual({ tags: ['chips', 'ai'] });
    expect(data.tags).toEqual(['chips', 'ai']);
  });

  it('setTags sends an empty array rather than omitting the field', async () => {
    // Clearing every tag has to be expressible, or a tag can never be removed.
    const api = mockApi([['/api/watchlist/INTC', { symbol: 'INTC', tags: [] }]]);
    await watchlistApi.setTags('INTC', []);
    expect(JSON.parse(String(api.calls[0][1]?.body))).toEqual({ tags: [] });
  });

  it('setTags url-encodes a symbol with a dot', async () => {
    const api = mockApi([[/\/api\/watchlist\//, { symbol: 'BRK.B', tags: [] }]]);
    await watchlistApi.setTags('BRK.B', []);
    expect(api.calls[0][0]).toContain('/api/watchlist/BRK.B');
  });

  it('surfaces a server error instead of resolving', async () => {
    mockApi([['/api/watchlist/NOPE', { __status: 404, error: 'NOPE not found in watchlist' }]]);
    await expect(watchlistApi.setTags('NOPE', ['x'])).rejects.toThrow(/not found/i);
  });
});
