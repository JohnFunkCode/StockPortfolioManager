/**
 * /api/portfolio/orders client (#431): a paper book's orders. The system sets
 * every fill, so nothing here sends a price or a date.
 */
import { apiRequest } from './client';

export type OrderStatus = 'pending' | 'filled' | 'cancelled' | 'rejected';

export type OrderKind = 'market' | 'seed' | 'manual_close';

export type RejectReason =
  | 'stale_quote'
  | 'no_quote'
  | 'non_usd'
  | 'insufficient_cash'
  | 'insufficient_shares'
  | 'no_open_price';

export interface Order {
  order_id: number;
  book_id: number;
  symbol: string;
  side: 'buy' | 'sell';
  /** A buy asks for shares or for a dollar amount. A sell asks for shares. */
  requested_shares: number | null;
  requested_amount: number | null;
  status: OrderStatus;
  /** What prompted the trade: manual, stop, harvester, evaluator, watch or ai. */
  source: string;
  source_ref: string | null;
  /** The session the order filled in, or the one a pending order waits for. */
  session_date: string;
  placed_at: string;
  resolved_at: string | null;
  filled_shares: number | null;
  fill_price: number | null;
  /** Null on a manual close: its price is the last one on record. */
  price_source: 'quote' | 'session_open' | null;
  /** The time of the trade, or the daily bar, the fill price came from. */
  quote_time: string | null;
  /** market (moves cash), seed (a real holding copied in at no cost) or
   * manual_close (sold at the last price on record: no quote). */
  kind: OrderKind;
  reject_reason: RejectReason | null;
  /** True when the request repeated an idempotency key: nothing new happened. */
  replayed: boolean;
}

export interface OrdersResponse {
  book_kind: 'real' | 'paper';
  pending_count: number;
  /** Newest first. */
  orders: Order[];
}

export interface PlaceOrderPayload {
  book_id: number;
  symbol: string;
  side: 'buy' | 'sell';
  /** Give `shares` or, for a buy, `amount` in dollars. Never both. */
  shares?: number;
  amount?: number;
  /** The caller's own id for this request. Sending it again places nothing. */
  idempotency_key: string;
}

/** What copying the real book's holdings into a paper book did. */
export interface SeedResult {
  book_id: number;
  filled: number;
  /** Queued for the next session: asked for outside market hours. */
  pending: number;
  rejected: number;
  /** Real holdings whose symbol an order can't name. */
  skipped: string[];
  orders: Order[];
}

export interface ManualClosePayload {
  book_id: number;
  symbol: string;
  idempotency_key: string;
}

/** An id for one request. Sending the same one twice places one order. */
export function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const ordersApi = {
  list: (bookId: number) =>
    apiRequest<OrdersResponse>(`/api/portfolio/orders?book_id=${bookId}`),

  /** A rejected order is an answer, not an error: check `status`. */
  place: (data: PlaceOrderPayload) =>
    apiRequest<Order>('/api/portfolio/orders', { method: 'POST', body: JSON.stringify(data) }),

  /** Copy the real book's open holdings into a paper book with no orders. */
  seed: (bookId: number) =>
    apiRequest<SeedResult>('/api/portfolio/orders/seed', {
      method: 'POST',
      body: JSON.stringify({ book_id: bookId }),
    }),

  /** Sell a whole position that can't be quoted, at the last price on record. */
  manualClose: (data: ManualClosePayload) =>
    apiRequest<Order>('/api/portfolio/orders/manual-close', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Only a pending order can be cancelled. */
  cancel: (bookId: number, orderId: number) =>
    apiRequest<Order>(`/api/portfolio/orders/${orderId}/cancel?book_id=${bookId}`, {
      method: 'POST',
    }),
};
