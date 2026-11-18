import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { booksApi, performanceApi } from '../api/books';
import type { CreateBookPayload, PerformancePeriod } from '../api/books';

export function useBooks() {
  return useQuery({
    queryKey: ['books'],
    queryFn: () => booksApi.list(),
    staleTime: 60 * 1000,
  });
}

export function useCreateBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateBookPayload) => booksApi.create(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['books'] }),
  });
}

export function useArchiveBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: number) => booksApi.archive(bookId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['books'] }),
  });
}

/** One book's performance against SPY (#431 step 5.3). The summary strip and
 * the Performance section both ask for the year, so they share one request
 * until the section's period is changed. */
export function useBookPerformance(period: PerformancePeriod, bookId: number | null = null) {
  return useQuery({
    queryKey: ['book-performance', period, bookId],
    queryFn: () => performanceApi.get(period, bookId),
    // The rows change once a night, after the daily job.
    staleTime: 5 * 60 * 1000,
  });
}

/** Every open book side by side: the Compare books view. */
export function useCompareBooks(period: PerformancePeriod) {
  return useQuery({
    queryKey: ['book-performance-compare', period],
    queryFn: () => performanceApi.compare(period),
    staleTime: 5 * 60 * 1000,
  });
}
