import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { cashApi } from '../api/cash';
import type { CashEntryPayload, OpeningBalancePayload } from '../api/cash';

/** The cash ledger of one book; null is the real book (#431). */
export function useCashLedger(bookId: number | null = null) {
  return useQuery({
    queryKey: ['portfolio-cash', bookId],
    queryFn: () => cashApi.getLedger(bookId),
    staleTime: 60 * 1000,
  });
}

function useCashMutation<T>(mutationFn: (arg: T) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portfolio-cash'] }),
  });
}

export function useSetOpeningBalance() {
  return useCashMutation((payload: OpeningBalancePayload) => cashApi.setOpeningBalance(payload));
}

export function useAddCashEntry() {
  return useCashMutation((payload: CashEntryPayload) => cashApi.addEntry(payload));
}

export function useDeleteCashEntry() {
  return useCashMutation((entryId: number) => cashApi.deleteEntry(entryId));
}
