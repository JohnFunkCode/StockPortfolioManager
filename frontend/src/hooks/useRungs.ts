import { useMutation, useQueryClient } from '@tanstack/react-query';
import { rungsApi } from '../api/rungs';
import type { ExecuteRungRequest } from '../api/types';

export function useAchieveRung() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ rungId, triggerPrice }: { rungId: number; triggerPrice: number }) =>
      rungsApi.achieve(rungId, triggerPrice),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['plans'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

export function useExecuteRung() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ rungId, data }: { rungId: number; data: ExecuteRungRequest }) =>
      rungsApi.execute(rungId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['plans'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}
