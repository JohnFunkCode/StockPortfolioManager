import { apiRequest } from './client';
import type { Plan, PlanWithRungs, CreatePlanRequest } from './types';

export const plansApi = {
  list: (status: string = 'ACTIVE') =>
    apiRequest<{ plans: Plan[] }>(`/api/plans?status=${status}`),

  get: (id: number) =>
    apiRequest<PlanWithRungs>(`/api/plans/${id}`),

  create: (data: CreatePlanRequest) =>
    apiRequest<Plan>('/api/plans', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: number, data: { notes?: string; metadata?: Record<string, unknown> }) =>
    apiRequest<{ instance_id: number; updated: boolean }>(`/api/plans/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  delete: (id: number) =>
    apiRequest<{ instance_id: number; deleted: boolean }>(`/api/plans/${id}`, {
      method: 'DELETE',
    }),
};
