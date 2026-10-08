import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { portfolioApi } from '../api/portfolio';
import type {
  CloseLotPayload,
  CreateLotPayload,
  UpdateLotPayload,
  UpdateSalePayload,
} from '../api/portfolioTypes';

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
  qc.invalidateQueries({ queryKey: ['portfolio-sales'] });
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

/** Which lots a sale of `shares` would touch — drives the per-lot note fields. */
export function useClosePreview(lotId: number, shares: number) {
  return useQuery({
    queryKey: ['portfolio-close-preview', lotId, shares],
    queryFn: () => portfolioApi.previewClose(lotId, shares),
    enabled: shares > 0,
    staleTime: 30 * 1000,
    retry: false,
  });
}

export function useSales(symbol?: string) {
  return useQuery({
    queryKey: ['portfolio-sales', symbol],
    queryFn: () => portfolioApi.getSales(symbol),
    staleTime: 60 * 1000,
  });
}

export function useUpdateSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ saleId, data }: { saleId: number; data: UpdateSalePayload }) =>
      portfolioApi.updateSale(saleId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portfolio-sales'] }),
  });
}
