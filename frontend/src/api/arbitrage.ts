/**
 * Arbitrage scanner client — GET /api/arbitrage/*.
 *
 * Only the single-pair workup is wired to the UI today; the scan and
 * discovery endpoints are driven from MCP and the sidekick's own tools.
 */
import { apiRequest } from './client';

export interface ArbitrageFactors {
  opportunity: number | null;
  evidence: number | null;
  convergence: number | null;
  hedge: number | null;
  carry: number | null;
  trend: number | null;
  freshness: number | null;
}

export interface ArbitrageNav {
  available: boolean;
  reason?: string;
  /** NET of senior claims — the figure a common shareholder actually gets. */
  premium_discount_pct?: number | null;
  /** Gap to GROSS assets. Reported only so the difference is visible. */
  gross_premium_discount_pct?: number | null;
  carry_drag_pct?: number | null;
  years_of_burn?: number | null;
  exposure_ratio?: number | null;
  nav_per_share?: number | null;
  holdings_as_of?: string | null;
  holdings_age_days?: number | null;
  holdings_stale?: boolean;
}

export interface ArbitrageHedge {
  instrument: string | null;
  available: boolean;
  futures_only: boolean;
  note: string;
}

export interface ArbitrageStatistics {
  n: number;
  hedge_ratio: { beta: number | null; beta_stability: number | null; r_squared: number | null };
  cointegration: { cointegrated: boolean; reject_at: number | null; statistic: number | null };
  zscore: { z: number | null };
  half_life: { half_life_days: number | null; mean_reverting: boolean };
  trend: { widening: boolean | null; slope_tstat: number | null };
  correlation: number | null;
}

export interface ArbitragePairResponse {
  security: string;
  name?: string;
  kind: string;
  underlying: string;
  as_of?: string | null;
  price?: number | null;
  underlying_price?: number | null;
  statistics: ArbitrageStatistics;
  nav: ArbitrageNav | null;
  hedge: ArbitrageHedge;
  score: number | null;
  verdict: string;
  basis: string | null;
  convergence: string;
  factors?: ArbitrageFactors;
  reasons: string[];
  breaks_on: string[];
  notes?: string | null;
  /** Present instead of the analysis when the pair can't be resolved. */
  error?: string;
  hint?: string;
}

export interface SpreadPoint {
  date: string;
  spread: number;
}

export interface SpreadHistoryResponse {
  security: string;
  name?: string;
  kind: string;
  underlying: string;
  points: SpreadPoint[];
  mean: number;
  std: number;
  /** Precomputed band levels — the chart draws, it does not derive. */
  bands: {
    plus_one: number;
    minus_one: number;
    plus_two: number;
    minus_two: number;
  };
  latest: { date: string | null; spread: number | null; z: number | null };
  hedge_ratio: { beta: number | null; alpha: number | null };
  half_life: { half_life_days: number | null };
  cointegration: { cointegrated: boolean; statistic: number | null };
  trend: {
    slope_per_day: number | null;
    intercept: number | null;
    slope_tstat: number | null;
    widening: boolean | null;
  };
  error?: string;
  hint?: string;
}

export interface PremiumPoint {
  date: string;
  premium_discount_pct: number;
  nav_per_share: number;
  price: number;
}

export interface PremiumHistoryResponse {
  security: string;
  name?: string;
  underlying: string;
  points: PremiumPoint[];
  latest: PremiumPoint | null;
  holdings_as_of?: string | null;
  holdings_age_days?: number | null;
  holdings_stale?: boolean;
  /** Always "current_capital_structure" today — the series is an approximation. */
  method: string;
  note: string;
  error?: string;
}

export interface ScanRow {
  security: string;
  name?: string;
  kind: string;
  underlying: string;
  score: number | null;
  verdict: string;
  basis: string | null;
  discount_pct: number | null;
  spread_zscore: number | null;
  half_life_days: number | null;
  cointegrated: boolean | null;
  convergence: string;
  hedge_instrument: string | null;
  hedge_available: boolean;
  breaks_on: string[];
}

export interface ScanResponse {
  scanned: number;
  returned: number;
  candidates: ScanRow[];
  errors: Array<{ security: string; error: string }>;
}

export interface TestedPair {
  security: string;
  underlying: string;
  economic_link: boolean;
  correlation: number | null;
  statistic: number | null;
  reject_at: number | null;
  half_life_days: number | null;
  passed: boolean;
  failed_because: string | null;
}

export interface DiscoveryResponse {
  count: number;
  pairs: Array<{ security: string; underlying: string; correlation: number | null }>;
  skipped: Array<{ symbol: string; reason: string }>;
  references: string[];
  /** Coverage accounting: these two partition the submitted ticker list. */
  symbols_requested?: string[];
  symbols_tested?: string[];
  /** Present only with include_all — every tested pair, near-misses included. */
  tested?: TestedPair[];
  critical_values?: Record<string, number>;
}

export const arbitrageApi = {
  getPair: (security: string, days = 365) =>
    apiRequest<ArbitragePairResponse>(
      `/api/arbitrage/pairs/${encodeURIComponent(security)}?days=${days}`,
    ),

  getSpreadHistory: (security: string, days = 365) =>
    apiRequest<SpreadHistoryResponse>(
      `/api/arbitrage/pairs/${encodeURIComponent(security)}/spread-history?days=${days}`,
    ),

  getPremiumHistory: (security: string, days = 365) =>
    apiRequest<PremiumHistoryResponse>(
      `/api/arbitrage/pairs/${encodeURIComponent(security)}/premium-history?days=${days}`,
    ),

  scan: (topN = 20, days = 365) =>
    apiRequest<ScanResponse>(`/api/arbitrage/scan?top_n=${topN}&days=${days}`),

  discover: (symbols: string, requireEconomicLink = true) =>
    apiRequest<DiscoveryResponse>(
      `/api/arbitrage/discover?symbols=${encodeURIComponent(symbols)}` +
        `&include_all=true&require_economic_link=${requireEconomicLink}`,
    ),
};
