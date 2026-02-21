import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { plansApi } from '../api/plans';
import type { CreatePlanRequest } from '../api/types';

export function usePlans(status: string = 'ACTIVE') {
  return useQuery({
    queryKey: ['plans', status],
    queryFn: () => plansApi.list(status),
  });
}

export function usePlan(id: number) {
  return useQuery({
    queryKey: ['plans', id],
    queryFn: () => plansApi.get(id),
    enabled: !!id,
  });
}

export function useCreatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreatePlanRequest) => plansApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['plans'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

export function useUpdatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: { notes?: string; metadata?: Record<string, unknown> } }) =>
      plansApi.update(id, data),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['plans', id] });
      qc.invalidateQueries({ queryKey: ['plans'] });
    },
  });
}

export function useDeletePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => plansApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['plans'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}
