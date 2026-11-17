/** A canned GET /api/portfolio/check response for the Portfolio Check tests (made-up holdings). */
import type { PortfolioCheckHolding, PortfolioCheckResponse } from '../../api/portfolioCheck';

export function checkHolding(overrides: Partial<PortfolioCheckHolding> = {}): PortfolioCheckHolding {
  return {
    ticker: 'KO',
    company: 'Coca Cola Co',
    action: 'Sell',
    industry_code: 'Beer',
    industry: 'Beer & Liquor',
    industry_label: 'Sell',
    industry_rank: 47,
    industry_pct: 0.06,
    shares: 70,
    value: 6800,
    cost: 4046,
    weight: 0.043,
    industry_weight: 0.043,
    vs_peers_12m: 0.12,
    vs_peers_percentile: 0.7,
    peer_count: 5,
    reasons: [
      'Beer & Liquor ranks 47 of 49 industries by 12-month wind (-18% vs market). It has been in the bottom 40% at two reviews in a row.',
      'Historically, industries with this label trailed the market by about 1.8% over the next 12 months (1931–2026).',
    ],
    tax_note: 'Selling now realizes $2,754 long-term and $0 short-term gain/loss; estimated tax $413.',
    ...overrides,
  };
}

export function checkReport(overrides: Partial<PortfolioCheckResponse> = {}): PortfolioCheckResponse {
  return {
    as_of: '2026-11-17',
    params: { st_rate: 0.32, lt_rate: 0.15, position_cap: 0.1, industry_cap: 0.25, wait_days: 120 },
    market_state: {
      generated: '2026-11-17',
      industries_asof: '2026-09',
      window: { a: '2025-11-14', b: '2026-10-19', prices_asof: '2026-11-17' },
      sell_edge: { value: -0.0179, period: '1931–2026' },
    },
    total_value: 157930,
    holdings: [
      checkHolding(),
      checkHolding({
        ticker: 'XOM', company: 'Exxon Mobil Corp', action: 'Buy / add', industry_code: 'Oil',
        industry: 'Oil & Gas', industry_label: 'Buy zone', industry_rank: 4, vs_peers_12m: -0.05,
        reasons: ['Oil & Gas ranks 4 of 49 industries by 12-month wind (+21% vs market), in the top 20%.'],
        tax_note: '',
      }),
    ],
    action_counts: [{ action: 'Sell', count: 1 }, { action: 'Buy / add', count: 1 }],
    ideas: [
      {
        industry: 'Precious Metals', industry_code: 'Gold', rank: 1, wind12: 0.85,
        examples: [{ ticker: 'NEM', name: 'Newmont', vs_peers_12m: 0.1 }, { ticker: 'XYZ', name: 'Xyz Co', vs_peers_12m: null }],
      },
    ],
    industries: [
      { code: 'Gold', industry: 'Precious Metals', label: 'Buy zone', pct: 1, pct_prev: 0.98, wind12: 0.85, wind60: 1.2, bm: 0.4, rank: 1 },
      { code: 'Oil', industry: 'Oil & Gas', label: 'Buy zone', pct: 0.92, pct_prev: 0.9, wind12: 0.21, wind60: null, bm: 0.6, rank: 4 },
      { code: 'Beer', industry: 'Beer & Liquor', label: 'Sell', pct: 0.06, pct_prev: 0.1, wind12: -0.18, wind60: -0.3, bm: 0.25, rank: 47 },
    ],
    unpriced: [],
    ...overrides,
  };
}
