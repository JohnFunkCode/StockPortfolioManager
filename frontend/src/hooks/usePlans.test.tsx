import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';

import {
  useCreatePlan,
  useDeletePlan,
  usePlan,
  usePlans,
  useUpdatePlan,
} from './usePlans';
import { mockApi, renderHookWithProviders } from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('usePlans query hooks', () => {
  it('usePlans forwards the status filter', async () => {
    const api = mockApi([['/api/plans?status=ACTIVE', { plans: [{ instance_id: 1 }] }]]);
    const { result } = renderHookWithProviders(() => usePlans('ACTIVE'));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.plans[0].instance_id).toBe(1);
    expect(api.calls[0][0]).toContain('status=ACTIVE');
  });

  it('usePlan is disabled without an id and fetches when given one', async () => {
    const disabled = renderHookWithProviders(() => usePlan(0));
    expect(disabled.result.current.fetchStatus).toBe('idle');

    mockApi([['/api/plans/7', { plan: { instance_id: 7 }, rungs: [] }]]);
    const active = renderHookWithProviders(() => usePlan(7));
    await waitFor(() => expect(active.result.current.isSuccess).toBe(true));
    expect(active.result.current.data!.plan.instance_id).toBe(7);
  });
});

describe('usePlans mutation hooks', () => {
  it('useCreatePlan POSTs the plan body', async () => {
    const api = mockApi([['/api/plans', { instance_id: 9 }]]);
    const { result } = renderHookWithProviders(() => useCreatePlan());
    result.current.mutate({ symbol: 'INTC' } as never);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(api.calls[0][1]?.body)).symbol).toBe('INTC');
  });

  it('useUpdatePlan PATCHes by id', async () => {
    const api = mockApi([['/api/plans/3', { instance_id: 3, updated: true }]]);
    const { result } = renderHookWithProviders(() => useUpdatePlan());
    result.current.mutate({ id: 3, data: { notes: 'reviewed' } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][1]?.method).toBe('PATCH');
    expect(JSON.parse(String(api.calls[0][1]?.body)).notes).toBe('reviewed');
  });

  it('useDeletePlan DELETEs by id', async () => {
    const api = mockApi([['/api/plans/5', { instance_id: 5, deleted: true }]]);
    const { result } = renderHookWithProviders(() => useDeletePlan());
    result.current.mutate(5);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.calls[0][1]?.method).toBe('DELETE');
  });
});
