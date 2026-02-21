export interface Plan {
  instance_id: number;
  symbol: string;
  status: 'ACTIVE' | 'SUPERSEDED';
  created_at: string;
  asof_date: string;
  price_asof: number;
  shares_initial: number;
  v0_floor: number;
  capital_at_risk: number;
  h_threshold: number;
  n_iterations: number;
  annual_vol: number;
  r_daily: number;
  history_end_date: string;
  history_window_days: number;
  template_id: number;
  symbol_id: number;
  position_id: number | null;
  stats_price_series: string;
  supersedes_instance_id: number | null;
  notes: string | null;
  metadata_json: string | null;
}

export interface Rung {
  rung_id: number;
  instance_id: number;
  rung_index: number;
  target_price: number;
  shares_before: number;
  shares_sold_planned: number;
  shares_after_planned: number;
  expected_days_from_now: number | null;
  expected_date: string | null;
  gross_harvest_planned: number;
  cumulative_harvest_planned: number;
  remaining_value_planned: number;
  total_wealth_planned: number;
  total_return_planned: number;
  status: 'PENDING' | 'ACHIEVED' | 'EXECUTED';
  triggered_at: string | null;
  trigger_price: number | null;
  executed_at: string | null;
  executed_price: number | null;
  shares_sold_actual: number | null;
  gross_harvest_actual: number | null;
  tax_paid_actual: number | null;
  net_harvest_actual: number | null;
  notes: string | null;
}

export interface PlanWithRungs {
  plan: Plan;
  rungs: Rung[];
}

export interface SymbolInfo {
  symbol_id: number;
  ticker: string;
  name: string | null;
  currency: string | null;
  active_plan_id: number | null;
}

export interface DashboardStats {
  total_plans: number;
  active_plans?: number;
  superseded_plans?: number;
  pending_rungs?: number;
  achieved_rungs?: number;
  executed_rungs?: number;
  symbols_tracked: number;
  active_alerts: number;
}

export interface CreatePlanRequest {
  symbol: string;
  template_name?: string;
  params?: {
    history_window_days?: number;
    n_iterations?: number;
    alpha?: number;
    min_H?: number;
    max_H?: number;
    max_s0?: number;
  };
}

export interface ExecuteRungRequest {
  executed_price: number;
  shares_sold: number;
  tax_paid?: number;
  notes?: string;
}
