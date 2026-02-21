import { apiRequest } from './client';
import type { Rung, ExecuteRungRequest } from './types';

export const rungsApi = {
  list: (planId: number) =>
    apiRequest<{ rungs: Rung[] }>(`/api/plans/${planId}/rungs`),

  get: (rungId: number) =>
    apiRequest<{ rung: Rung }>(`/api/rungs/${rungId}`),

  achieve: (rungId: number, triggerPrice: number) =>
    apiRequest<{ rung_id: number; status: string }>(`/api/rungs/${rungId}/achieve`, {
      method: 'POST',
      body: JSON.stringify({ trigger_price: triggerPrice }),
    }),

  execute: (rungId: number, data: ExecuteRungRequest) =>
    apiRequest<{ rung_id: number; status: string }>(`/api/rungs/${rungId}/execute`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};
