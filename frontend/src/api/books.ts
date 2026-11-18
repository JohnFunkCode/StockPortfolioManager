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

/** The rolling windows a performance read covers (the Realized view's). */
export type PerformancePeriod = 'month' | 'quarter' | 'year' | 'all';

/** One valued session. `index` is the growth of $1 in the book since the
 * first point, time-weighted; `benchmark_index` the same for SPY. */
export interface PerformancePoint {
  session_date: string;
  book_value: number | null;
  index: number;
  benchmark_index: number | null;
}

/** Realized P/L on the sales one source prompted. */
export interface SourceRealized {
  source?: string | null;
  sale_count: number;
  fx_missing_count: number;
  basis_missing_count: number;
  cost_basis: number | null;
  proceeds: number | null;
  realized_pnl: number | null;
  realized_pnl_pct: number | null;
}

/** GET /api/books/performance (#431 step 5.3). Returns are fractions. */
export interface BookPerformance {
  book_id: number | null;
  name: string;
  kind: 'real' | 'paper';
  period: PerformancePeriod;
  start_date: string | null;
  end_date: string | null;
  first_session: string | null;
  last_session: string | null;
  twr: number | null;
  benchmark_symbol: string;
  benchmark_available: boolean;
  benchmark_return: number | null;
  vs_benchmark: number | null;
  max_drawdown: number | null;
  drawdown_peak: string | null;
  drawdown_trough: string | null;
  series: PerformancePoint[];
  gaps: string[];
  restated_count: number;
  last_restated_at: string | null;
  cash_negative_count: number;
  latest: {
    session_date: string;
    book_value: number | null;
    cash: number | null;
    holdings_value: number | null;
    unpriced_count: number;
    stale_count: number;
    fx_missing_count: number;
  } | null;
  attribution: { sources: SourceRealized[]; totals: SourceRealized };
}

export interface CompareBooks {
  period: PerformancePeriod;
  start_date: string | null;
  end_date: string | null;
  books: BookPerformance[];
}

export const performanceApi = {
  /** One book's performance. No book id is the real book. */
  get: (period: PerformancePeriod, bookId: number | null = null) => {
    const params = new URLSearchParams({ period });
    if (bookId !== null) params.set('book_id', String(bookId));
    return apiRequest<BookPerformance>(`/api/books/performance?${params}`);
  },

  /** Every open book over one period, the real book first. */
  compare: (period: PerformancePeriod) =>
    apiRequest<CompareBooks>(`/api/books/performance/compare?period=${period}`),
};
