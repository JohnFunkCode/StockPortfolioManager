import { useQuery } from '@tanstack/react-query';
import { stopsApi } from '../api/stops';

/** Every stop one book has had, newest first; null is the real book (#431).
 * The triggered ones are included, so a holding whose stop was reached can
 * say so. */
export function useStops(bookId: number | null = null) {
  return useQuery({
    queryKey: ['portfolio-stops', bookId],
    queryFn: () => stopsApi.list(bookId, true),
    staleTime: 60 * 1000,
  });
}
