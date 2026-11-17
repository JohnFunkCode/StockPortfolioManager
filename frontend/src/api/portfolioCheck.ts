/**
 * Portfolio Check client — GET /api/portfolio/check.
 *
 * Always the caller's own open lots: the API resolves the owner from the
 * authenticated session, so there is no owner argument here and never should
 * be. Every number is computed server-side (quantcore/analytics/portfolio_check.py).
 */
import { apiRequest } from './client';

export interface PortfolioCheckHolding {
  ticker: string;
  company: string;
  action: string;
  industry_code: string | null;
  industry: string;
  industry_label: string | null;
  industry_rank: number | null;
  industry_pct: number | null;
  shares: number | null;
  value: number | null;
  cost: number | null;
  weight: number | null;
  industry_weight: number | null;
  vs_peers_12m: number | null;
  vs_peers_percentile: number | null;
  peer_count: number;
  reasons: string[];
  tax_note: string;
}

export interface PortfolioCheckIdea {
  industry: string;
  industry_code: string;
  rank: number;
  wind12: number | null;
  examples: { ticker: string; name: string; vs_peers_12m: number | null }[];
}

export interface PortfolioCheckIndustry {
  code: string;
  industry: string;
  label: string;
  pct: number;
  pct_prev: number;
  wind12: number | null;
  wind60: number | null;
  bm: number | null;
  rank: number;
}

export interface PortfolioCheckResponse {
  as_of: string;
  params: Record<string, number>;
  market_state: {
    generated: string;
    industries_asof: string;
    window: { a: string; b: string; prices_asof: string };
    sell_edge: { value: number; period: string };
  };
  total_value: number | null;
  holdings: PortfolioCheckHolding[];
  /** How many holdings got each action, in report order. */
  action_counts: { action: string; count: number }[];
  ideas: PortfolioCheckIdea[];
  industries: PortfolioCheckIndustry[];
  unpriced: string[];
}

export const portfolioCheckApi = {
  getCheck: () => apiRequest<PortfolioCheckResponse>('/api/portfolio/check'),
};
