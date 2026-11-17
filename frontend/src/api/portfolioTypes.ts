export interface Lot {
  lot_id: number;
  symbol: string;
  name: string;
  status: string | null;
  parent_lot_id: number | null;
  purchase_price: number | null;
  quantity: number | null;
  purchase_date: string | null;
  trade_date: string | null;
  currency: string;
  sale_price: number | null;
  sale_date: string | null;
  fees: number | null;
  acquisition_type: string | null;
  account: string | null;
  notes: string | null;
  current_price: number | null;
  price_as_of: string | null;
  price_stale: boolean;
  gain_loss: number | null;
  gain_loss_pct: number | null;
  days_held: number | null;
  dollars_per_day: number | null;
}

export interface SymbolRow {
  symbol: string;
  lots: Lot[];
  /** Shares held across every open lot, priced or not. Server-computed. */
  total_quantity: number | null;
  total_investment: number | null;
  total_current_value: number | null;
  total_gain_loss: number | null;
  total_gain_loss_pct: number | null;
  total_dollars_per_day: number | null;
  // Return windows (issue #518). 1d–60d count trading bars, not calendar
  // days; YTD and 1y are calendar-anchored. All percent, all server-computed.
  return_1d: number | null;
  return_5d: number | null;
  return_30d: number | null;
  return_60d: number | null;
  return_ytd: number | null;
  return_1y: number | null;
  // Live 1d columns. return_1d_display is the figure to show: the quote's
  // regular-session change when fresh, else the cached return_1d with
  // return_1d_stale true. The page renders it and computes nothing.
  regular_change_pct: number | null;
  pre_change_pct: number | null;
  post_change_pct: number | null;
  market_state: string | null;
  quote_as_of: string | null;
  quote_stale: boolean;
  /** A live quote whose last trade is from before the current session. */
  quote_earlier_session?: boolean;
  return_1d_display: number | null;
  return_1d_stale: boolean;
  mm_hedge_bias: string | null;
  mm_hedge_bias_captured_at: string | null;
  // Stacked allocation bar (issue #147 Part A), computed server-side by
  // quantcore.analytics.portfolio_math.allocation_segments. bar_loss is
  // negative — it is drawn *down* from bar_base — and all three are null for a
  // symbol with no priced lot, which the chart drops rather than flattens.
  bar_base: number | null;
  bar_gain: number | null;
  bar_loss: number | null;
  // The owner's ACTIVE harvest plan on this symbol (issue #147 Part H6), or
  // null when there is no ladder yet — which the page turns into a create
  // affordance rather than an empty cell.
  active_plan_id: number | null;
}

export interface PortfolioTotals {
  total_investment: number | null;
  total_current_value: number | null;
  total_gain_loss: number | null;
  total_gain_loss_pct: number | null;
  total_dollars_per_day: number | null;
}

export interface CreateLotPayload {
  symbol: string;
  name?: string;
  currency?: string;
  purchase_price?: number;
  quantity?: number;
  trade_date?: string;
  fees?: number;
  acquisition_type?: string;
  account?: string;
  notes?: string;
}

export interface UpdateLotPayload {
  name?: string;
  purchase_price?: number;
  quantity?: number;
  purchase_date?: string;
  currency?: string;
  trade_date?: string;
  fees?: number;
  acquisition_type?: string;
  account?: string;
  notes?: string;
}

export interface CloseLotPayload {
  shares: number;
  sale_price: number;
  sale_trade_date: string;
  method?: string;
  lots?: Array<[number, number]>;
  fees?: number;
  /** Why the shares were sold — the default note for every lot the sale touches. */
  notes?: string;
  /** Per-lot overrides of `notes`, keyed by lot_id. */
  lot_notes?: Record<number, string>;
}

/** Longest note the API accepts (api/schemas/portfolio.py NOTE_MAX_LENGTH). */
export const NOTE_MAX_LENGTH = 5000;

export interface CreateLotResponse {
  symbol: string;
}

export interface UpdateLotResponse {
  lot_id: number;
  updated: boolean;
}

export interface DeleteLotResponse {
  lot_id: number;
  deleted: boolean;
}

export interface CloseLotAllocation {
  lot_id: number;
  sale_id: number;
  shares_sold: number;
  child_lot_id: number | null;
  notes?: string | null;
}

/** One lot a prospective sale would draw from (POST .../close/preview). */
export interface ClosePreviewAllocation {
  lot_id: number;
  shares: number;
  trade_date: string | null;
  purchase_price: number | null;
}

export interface ClosePreviewResponse {
  symbol: string;
  allocations: ClosePreviewAllocation[];
}

/** One recorded sale of (part of) a lot — a lot_sales row. */
export interface Sale {
  sale_id: number;
  lot_id: number;
  symbol: string;
  shares_sold: number;
  sale_price: number;
  sale_trade_date: string;
  fees: number | null;
  allocation_method: string | null;
  notes: string | null;
}

export interface UpdateSalePayload {
  notes: string | null;
}

export interface UpdateSaleResponse {
  sale_id: number;
  updated: boolean;
}

export interface CloseLotResponse {
  symbol: string;
  shares_sold: number;
  sale_price: number;
  sale_trade_date: string;
  allocations: CloseLotAllocation[];
}

/** The Realized view's period (#235): rolling windows of calendar days ending
 * today. The server resolves the dates; the page only names the period. */
export type RealizedPeriod = 'month' | 'quarter' | 'year' | 'all';

/** Sums over the sales that have USD figures. A money field is null, never
 * zero, when nothing could be summed. */
export interface RealizedTotals {
  sale_count: number;
  fx_missing_count: number;
  basis_missing_count: number;
  cost_basis: number | null;
  proceeds: number | null;
  realized_pnl: number | null;
  realized_pnl_pct: number | null;
}

/** One sale with its realized P/L. `cost_basis`, `proceeds` and `realized_pnl`
 * are USD and gross of fees; the `*_native` figures are in `currency`. A sale
 * flagged `fx_missing` or `basis_missing` has no USD figures and is in no total. */
export interface RealizedSale {
  sale_id: number;
  lot_id: number;
  symbol: string;
  currency: string;
  account: string | null;
  /** The purchase's trade date. */
  trade_date: string | null;
  sale_trade_date: string;
  shares_sold: number;
  purchase_price: number | null;
  sale_price: number;
  fx_rate_at_purchase: number | null;
  fx_rate_at_sale: number | null;
  cost_basis_native: number | null;
  proceeds_native: number | null;
  realized_pnl_native: number | null;
  cost_basis: number | null;
  proceeds: number | null;
  realized_pnl: number | null;
  realized_pnl_pct: number | null;
  fx_missing: boolean;
  basis_missing: boolean;
  allocation_method: string | null;
  notes: string | null;
  /** What prompted the sale: manual, stop, harvester, evaluator, watch or ai. */
  source: string;
  source_ref: string | null;
  /** The paper order that made the sale; null for one recorded by hand. */
  order_id: number | null;
}

export interface RealizedSymbolRow extends RealizedTotals {
  symbol: string;
  /** The book still has an open lot of this symbol: a partial exit. */
  still_held: boolean;
  shares_closed: number;
  // The allocation chart's grammar: base is the cost basis of the shares
  // closed, the gain or (negative) loss is the realized P/L.
  bar_base: number | null;
  bar_gain: number | null;
  bar_loss: number | null;
  sales: RealizedSale[];
}

/** GET /api/portfolio/realized. Both dates are null for `all`. */
export interface RealizedResponse {
  period: RealizedPeriod;
  start_date: string | null;
  end_date: string | null;
  symbols: RealizedSymbolRow[];
  totals: RealizedTotals & { symbol_count: number };
}
