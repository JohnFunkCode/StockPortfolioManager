import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { portfolioApi } from '../api/portfolio';
import type { CloseLotPayload, CreateLotPayload, UpdateLotPayload } from '../api/portfolioTypes';

export function useSymbolRows(force = false) {
  return useQuery({
    queryKey: ['portfolio-symbols', force],
    queryFn: () => portfolioApi.getSymbolRows(force),
    staleTime: 60 * 1000,
  });
}

function invalidatePortfolio(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['portfolio-symbols'] });
  qc.invalidateQueries({ queryKey: ['portfolio-lots'] });
}

export function useCreateLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateLotPayload) => portfolioApi.createLot(payload),
    onSuccess: () => invalidatePortfolio(qc),
  });
}

export function useUpdateLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ lotId, data }: { lotId: number; data: UpdateLotPayload }) =>
      portfolioApi.updateLot(lotId, data),
    onSuccess: () => invalidatePortfolio(qc),
  });
}

export function useDeleteLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lotId: number) => portfolioApi.deleteLot(lotId),
    onSuccess: () => invalidatePortfolio(qc),
  });
}

export function useCloseLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ lotId, data }: { lotId: number; data: CloseLotPayload }) =>
      portfolioApi.closeLot(lotId, data),
    onSuccess: () => invalidatePortfolio(qc),
  });
}
