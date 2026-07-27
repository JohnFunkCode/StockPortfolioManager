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

export const arbitrageApi = {
  getPair: (security: string, days = 365) =>
    apiRequest<ArbitragePairResponse>(
      `/api/arbitrage/pairs/${encodeURIComponent(security)}?days=${days}`,
    ),
};
