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
  total_investment: number | null;
  total_current_value: number | null;
  total_gain_loss: number | null;
  total_gain_loss_pct: number | null;
  total_dollars_per_day: number | null;
  return_7d: number | null;
  return_30d: number | null;
  return_90d: number | null;
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
}

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
}

export interface CloseLotResponse {
  symbol: string;
  shares_sold: number;
  sale_price: number;
  sale_trade_date: string;
  allocations: CloseLotAllocation[];
}
