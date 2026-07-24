import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';

import { usePricePolling, useSymbols } from './useSymbols';
import { mockApi, renderHookWithProviders } from '../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('useSymbols hooks', () => {
  it('useSymbols fetches the symbols list', async () => {
    mockApi([['/api/symbols', { symbols: [{ symbol_id: 1, symbol: 'INTC' }] }]]);
    const { result } = renderHookWithProviders(() => useSymbols());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.symbols[0].symbol).toBe('INTC');
  });

  it('usePricePolling hits the price endpoint and is disabled without a ticker', async () => {
    const api = mockApi([['/api/symbols/INTC/price', { ticker: 'INTC', price: 118 }]]);
    const active = renderHookWithProviders(() => usePricePolling('INTC'));
    await waitFor(() => expect(active.result.current.isSuccess).toBe(true));
    expect(active.result.current.data!.price).toBe(118);
    expect(api.calls[0][0]).toContain('/api/symbols/INTC/price');

    const idle = renderHookWithProviders(() => usePricePolling(''));
    expect(idle.result.current.fetchStatus).toBe('idle');
  });
});
