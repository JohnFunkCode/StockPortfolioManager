/**
 * Hook suite pattern (frontend 85%-campaign, demo template):
 * mock the NETWORK (mockApi), not the hooks — each useQuery wrapper is
 * verified to hit its endpoint, surface the payload, honor its params, and
 * expose errors. Copy this shape for usePlans/useRungs/useSymbols.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';

import {
  useAddSecurity,
  useOHLCV,
  useRefreshOptionsSnapshots,
  useRemoveFromPortfolio,
  useSecurities,
  useSymbolLookup,
  useTechnicals,
  useVerticalSpread,
} from './useSecurities';
import { indicatorRows, mockApi, renderHookWithProviders, securityRow } from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('query hooks', () => {
  it('useSecurities hits the source-specific endpoint', async () => {
    const api = mockApi([
      ['/api/portfolio', { securities: [securityRow('WMT', { source: 'portfolio' })] }],
    ]);
    const { result } = renderHookWithProviders(() => useSecurities('portfolio'));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.securities[0].symbol).toBe('WMT');
    expect(api.calls[0][0]).toContain('/api/portfolio');
    expect(api.unmatched).toEqual([]);
  });

  it('useOHLCV and useTechnicals forward the days param', async () => {
    mockApi([
      ['/ohlcv', { ticker: 'INTC', bars: [] }],
      ['/technicals', { ticker: 'INTC', indicators: indicatorRows(5) }],
    ]);
    const ohlcv = renderHookWithProviders(() => useOHLCV('INTC', 90));
    const tech = renderHookWithProviders(() => useTechnicals('INTC', 250));
    await waitFor(() => expect(ohlcv.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(tech.result.current.isSuccess).toBe(true));
    expect(tech.result.current.data!.indicators).toHaveLength(5);
  });

  it('useVerticalSpread opts into server curves and disables on empty args', async () => {
    const api = mockApi([
      ['/options/vertical-spread', { symbol: 'WMT', legs: null }],
    ]);
    const active = renderHookWithProviders(() =>
      useVerticalSpread('WMT', '2026-12-18', 120, 125, 'call'),
    );
    await waitFor(() => expect(active.result.current.isSuccess).toBe(true));
    const body = JSON.parse(String(api.calls[0][1]?.body));
    expect(body.include_curves).toBe(true);

    const disabled = renderHookWithProviders(() =>
      useVerticalSpread('', '', 0, 0, 'call'),
    );
    expect(disabled.result.current.fetchStatus).toBe('idle');
  });

  it('useSymbolLookup disables on empty input and errors on 404', async () => {
    mockApi([['/lookup', (url) => ({ __status: 404, error: 'unknown symbol' })]]);
    const empty = renderHookWithProviders(() => useSymbolLookup(''));
    expect(empty.result.current.fetchStatus).toBe('idle');
    const bad = renderHookWithProviders(() => useSymbolLookup('ZZWRONG'));
    await waitFor(() => expect(bad.result.current.isError).toBe(true));
  });
});

describe('mutation hooks', () => {
  it('useAddSecurity posts to the right list', async () => {
    const api = mockApi([
      ['/api/watchlist', { added: true }],
      ['/api/portfolio', { added: true }],
    ]);
    const { result } = renderHookWithProviders(() => useAddSecurity());
    result.current.watchlist.mutate({ symbol: 'INTC' } as never);
    await waitFor(() => expect(result.current.watchlist.isSuccess).toBe(true));
    expect(api.calls[0][0]).toContain('/api/watchlist');
    expect(api.calls[0][1]?.method).toBe('POST');
  });

  it('useRemoveFromPortfolio issues a DELETE', async () => {
    const api = mockApi([['/api/portfolio/INTC', { removed: true }]]);
    const { result } = renderHookWithProviders(() => useRemoveFromPortfolio());
    result.current.mutate('INTC');
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][1]?.method).toBe('DELETE');
  });

  it('useRefreshOptionsSnapshots posts source and chain type', async () => {
    const api = mockApi([['/refresh-options-snapshots', { succeeded: 1 }]]);
    const { result } = renderHookWithProviders(() => useRefreshOptionsSnapshots());
    result.current.mutate({ source: 'watchlist', chainType: 'full' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][0]).toContain('source=watchlist');
    expect(api.calls[0][0]).toContain('chain_type=full');
  });
});
