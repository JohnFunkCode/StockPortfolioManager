/**
 * /api/books client (#431): the owner's books. A book is a named container of
 * positions — the one real book, or a paper book. It is not an account: a
 * lot's brokerage account is a field on the lot.
 */
import { apiRequest } from './client';

export interface Book {
  book_id: number;
  name: string;
  kind: 'real' | 'paper';
  starting_cash: number;
  created_at: string;
  archived_at: string | null;
}

export interface CreateBookPayload {
  name: string;
  starting_cash?: number;
}

export const booksApi = {
  /** The real book first. Archived books are left out. */
  list: () => apiRequest<{ books: Book[] }>('/api/books'),

  /** Create a paper book. */
  create: (data: CreateBookPayload) =>
    apiRequest<Book>('/api/books', { method: 'POST', body: JSON.stringify(data) }),

  /** Archive a paper book. Books are never deleted. */
  archive: (bookId: number) =>
    apiRequest<Book>(`/api/books/${bookId}/archive`, { method: 'POST' }),
};
