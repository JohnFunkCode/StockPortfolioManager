/**
 * /api/portfolio/cash client (#431): a book's cash ledger. Amounts are US
 * dollars, signed: positive when cash comes in. Every figure arrives computed.
 */
import { apiRequest } from './client';

export type CashEntryKind = 'opening_balance' | 'deposit' | 'withdrawal' | 'buy' | 'sell';

export interface CashEntry {
  entry_id: number;
  kind: CashEntryKind;
  entry_date: string;
  /** Null for a buy or sell of a non-USD lot with no exchange rate. */
  amount: number | null;
  fx_missing: boolean;
  /** The balance once this entry is applied. */
  balance_after: number;
  lot_id: number | null;
  sale_id: number | null;
  /** A buy or sell says what was traded, in the lot's own currency. */
  symbol: string | null;
  shares: number | null;
  price: number | null;
  currency: string | null;
  notes: string | null;
}

export interface CashLedger {
  /** A real book takes an opening balance, deposits and withdrawals. */
  book_kind: 'real' | 'paper';
  opening_date: string | null;
  opening_balance: number | null;
  /** Null until the book has an opening balance. */
  balance: number | null;
  negative: boolean;
  entry_count: number;
  /** Entries dated before the opening date: not listed, not counted. */
  earlier_entry_count: number;
  fx_missing_count: number;
  deposits: number | null;
  withdrawals: number | null;
  bought: number | null;
  sold: number | null;
  /** Newest first. */
  entries: CashEntry[];
}

export interface OpeningBalancePayload {
  date: string;
  amount: number;
  notes?: string;
}

export interface CashEntryPayload {
  kind: 'deposit' | 'withdrawal';
  date: string;
  /** Typed as a positive number; the kind gives it its sign. */
  amount: number;
  notes?: string;
}

export const cashApi = {
  /** No `bookId` means the real book: the parameter is left off (#431). */
  getLedger: (bookId?: number | null) =>
    apiRequest<CashLedger>(`/api/portfolio/cash${bookId != null ? `?book_id=${bookId}` : ''}`),

  /** The real book only. Replaces any earlier opening balance. */
  setOpeningBalance: (data: OpeningBalancePayload) =>
    apiRequest<CashLedger>('/api/portfolio/cash/opening-balance', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  /** The real book only. */
  addEntry: (data: CashEntryPayload) =>
    apiRequest<CashLedger>('/api/portfolio/cash/entries', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Removes a deposit or withdrawal. Nothing else can be deleted. */
  deleteEntry: (entryId: number) =>
    apiRequest<CashLedger>(`/api/portfolio/cash/entries/${entryId}`, { method: 'DELETE' }),
};
