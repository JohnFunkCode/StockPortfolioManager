import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';

import { useAchieveRung, useExecuteRung } from './useRungs';
import { mockApi, renderHookWithProviders } from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useRungs mutation hooks', () => {
  it('useAchieveRung POSTs the trigger price', async () => {
    const api = mockApi([['/api/rungs/4/achieve', { rung_id: 4, status: 'TRIGGERED' }]]);
    const { result } = renderHookWithProviders(() => useAchieveRung());
    result.current.mutate({ rungId: 4, triggerPrice: 120.5 });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][0]).toContain('/api/rungs/4/achieve');
    expect(api.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(api.calls[0][1]?.body)).trigger_price).toBe(120.5);
  });

  it('useExecuteRung POSTs the execution body', async () => {
    const api = mockApi([['/api/rungs/6/execute', { rung_id: 6, status: 'EXECUTED' }]]);
    const { result } = renderHookWithProviders(() => useExecuteRung());
    result.current.mutate({
      rungId: 6,
      data: { executed_price: 121.0, shares_sold: 10 } as never,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][0]).toContain('/api/rungs/6/execute');
    expect(JSON.parse(String(api.calls[0][1]?.body)).executed_price).toBe(121.0);
  });

  it('surfaces a server error', async () => {
    mockApi([['/api/rungs/9/achieve', () => ({ __status: 400, error: 'already triggered' })]]);
    const { result } = renderHookWithProviders(() => useAchieveRung());
    result.current.mutate({ rungId: 9, triggerPrice: 100 });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
