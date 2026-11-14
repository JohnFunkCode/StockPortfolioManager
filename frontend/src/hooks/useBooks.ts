import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { booksApi } from '../api/books';
import type { CreateBookPayload } from '../api/books';

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
