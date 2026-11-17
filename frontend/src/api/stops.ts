/**
 * /api/portfolio/stops client (#431, #221): the stops stored on a book's
 * holdings. Every level arrives worked out: a trailing stop's `stop_level` is
 * the server's, from its percentage and high-water mark.
 */
import { apiRequest } from './client';

export type StopKind = 'fixed' | 'trailing';

/** Only `active` is live. The rest are final. */
export type StopStatus = 'active' | 'triggered' | 'cancelled' | 'replaced' | 'closed';

/** How the level was chosen: typed, or one of the stop card's candidates. */
export type StopMethod = 'manual' | 'technical' | 'trailing' | 'confluence';

export interface Stop {
  stop_id: number;
  book_id: number;
  symbol: string;
  kind: StopKind;
  stop_level: number;
  /** A trailing stop: percent below `high_water_mark`. */
  trail_pct: number | null;
  high_water_mark: number | null;
  method: string;
  /** The level the stop card suggested, when it did. */
  proposed_level: number | null;
  /** The price the stop was set against, and that price's date. */
  reference_price: number | null;
  as_of: string;
  source: string;
  source_ref: string | null;
  status: StopStatus;
  created_at: string;
  resolved_at: string | null;
  /** The session a buy filled in at the open, for the stop that buy asked
   * for: its first check is against that day's bar. Null for a stop set by
   * hand, whose first check is against the next session to open. */
  first_session: string | null;
  /** The newest closed daily bar the stop has been tested against. */
  last_bar_checked: string | null;
  /** A triggered stop: the bar that reached it, and the price it was reached at. */
  triggered_session: string | null;
  trigger_price: number | null;
  /** The newest checked bar closed near the level (step 4.4): its session,
   * close and the ATR it was measured in. Null while it closed further away. */
  approached_session?: string | null;
  approach_close?: number | null;
  approach_atr?: number | null;
  /** The stop mirrors an order the owner placed at an outside broker (step
   * 4.5). QuantCore only records it. The details are what the owner gave,
   * and null when not given or not at a broker. */
  at_broker: boolean;
  broker_quantity: number | null;
  broker_order_type: BrokerOrderType | null;
  brokerage: string | null;
}

/** How a stop at a broker was placed. A trailing stop is a trailing_stop. */
export type BrokerOrderType = 'stop' | 'stop_limit' | 'trailing_stop';

/** POST /api/portfolio/stops: the stop for one holding. A fixed stop gives
 * `stop_level`, a trailing stop `trail_pct`. No book is the real book. */
export interface SetStopPayload {
  symbol: string;
  kind: StopKind;
  stop_level?: number;
  trail_pct?: number;
  method?: StopMethod;
  proposed_level?: number;
  book_id?: number;
  /** Real book only. The three details are optional, and refused without it. */
  at_broker?: boolean;
  broker_quantity?: number;
  broker_order_type?: BrokerOrderType;
  brokerage?: string;
}

/** The answer to a new stop: `replaced_stop_id` names the one it took over from. */
export interface SetStopResponse extends Stop {
  replaced_stop_id: number | null;
}

export interface StopsResponse {
  book_kind: 'real' | 'paper';
  active_count: number;
  /** Newest first. */
  stops: Stop[];
}

/** GET /api/portfolio/stops/holdings: one stop a holding at most. */
export interface HoldingStopsResponse {
  book_kind: 'real' | 'paper';
  /** In symbol order. Each is its symbol's live stop, or one a bar reached
   * while the holding was already open. The server picks. */
  stops: Stop[];
}

function withBook(path: string, bookId: number | null, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  if (bookId !== null) params.set('book_id', String(bookId));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export const stopsApi = {
  /** The stop each of a book's holdings shows; null is the real book. */
  holdings: (bookId: number | null = null) =>
    apiRequest<HoldingStopsResponse>(withBook('/api/portfolio/stops/holdings', bookId)),

  /** A book's stops; null is the real book. `includeInactive` adds the ones
   * that were triggered, cancelled, replaced or closed. */
  list: (bookId: number | null = null, includeInactive = false) =>
    apiRequest<StopsResponse>(
      withBook('/api/portfolio/stops', bookId, includeInactive ? { include_inactive: 'true' } : {}),
    ),

  /** Set the stop on a holding, replacing the one it has (step 4.5). */
  set: (payload: SetStopPayload) =>
    apiRequest<SetStopResponse>('/api/portfolio/stops', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Take an active stop off. Only an active stop can be cancelled. */
  cancel: (stopId: number, bookId: number | null = null) =>
    apiRequest<Stop>(withBook(`/api/portfolio/stops/${stopId}/cancel`, bookId), { method: 'POST' }),
};
