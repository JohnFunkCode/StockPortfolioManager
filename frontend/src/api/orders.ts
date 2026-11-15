/**
 * /api/portfolio/orders client (#431): a paper book's orders. The system sets
 * every fill, so nothing here sends a price or a date.
 */
import { apiRequest } from './client';

export type OrderStatus = 'pending' | 'filled' | 'cancelled' | 'rejected';

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
  price_source: 'quote' | 'session_open' | null;
  quote_time: string | null;
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

export const ordersApi = {
  list: (bookId: number) =>
    apiRequest<OrdersResponse>(`/api/portfolio/orders?book_id=${bookId}`),

  /** A rejected order is an answer, not an error: check `status`. */
  place: (data: PlaceOrderPayload) =>
    apiRequest<Order>('/api/portfolio/orders', { method: 'POST', body: JSON.stringify(data) }),

  /** Only a pending order can be cancelled. */
  cancel: (bookId: number, orderId: number) =>
    apiRequest<Order>(`/api/portfolio/orders/${orderId}/cancel?book_id=${bookId}`, {
      method: 'POST',
    }),
};
