import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { stopsApi } from '../api/stops';

/** The stop each holding of one book shows; null is the real book (#431).
 * The server picks: a symbol's live stop, or one a bar reached while the
 * holding was already open. `enabled` lets a caller wait until it is wanted. */
export function useHoldingStops(bookId: number | null = null, enabled = true) {
  return useQuery({
    queryKey: ['portfolio-stops', bookId],
    queryFn: () => stopsApi.holdings(bookId),
    staleTime: 60 * 1000,
    enabled,
  });
}

/** Take an active stop off one of a book's holdings. Only the stops are read
 * again: a cancel moves no cash and no shares. */
export function useCancelStop(bookId: number | null = null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stopId: number) => stopsApi.cancel(stopId, bookId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portfolio-stops'] }),
  });
}
