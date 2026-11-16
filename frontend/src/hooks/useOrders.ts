import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ordersApi } from '../api/orders';

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
