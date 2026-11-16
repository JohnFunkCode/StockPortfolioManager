import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ordersApi } from '../api/orders';
import type { ManualClosePayload, PlaceOrderPayload } from '../api/orders';

/** The orders of one paper book (#431). */
export function useOrders(bookId: number) {
  return useQuery({
    queryKey: ['portfolio-orders', bookId],
    queryFn: () => ordersApi.list(bookId),
    staleTime: 60 * 1000,
  });
}

/** Withdraw a pending order. A cancel moves no cash and no shares, so only
 * the orders are read again. */
export function useCancelOrder(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: number) => ordersApi.cancel(bookId, orderId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portfolio-orders'] }),
  });
}

/** A fill writes lots or sales and their cash rows, so everything a book
 * shows is read again. A queued or rejected order changes only the orders. */
function invalidateBook(qc: ReturnType<typeof useQueryClient>) {
  for (const key of [
    'portfolio-orders',
    'portfolio-symbols',
    'portfolio-lots',
    'portfolio-sales',
    'portfolio-realized',
    'portfolio-cash',
  ]) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}

/** Place a paper order. A rejected order is an answer, not an error. */
export function usePlaceOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PlaceOrderPayload) => ordersApi.place(payload),
    onSuccess: () => invalidateBook(qc),
  });
}

/** Copy the real book's holdings into a paper book that has had no orders. */
export function useSeedBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: number) => ordersApi.seed(bookId),
    onSuccess: () => invalidateBook(qc),
  });
}

/** Sell a whole unquotable position at the last price on record. */
export function useManualClose() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ManualClosePayload) => ordersApi.manualClose(payload),
    onSuccess: () => invalidateBook(qc),
  });
}
