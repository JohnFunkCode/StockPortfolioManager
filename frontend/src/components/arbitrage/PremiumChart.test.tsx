import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import PremiumChart from './PremiumChart';
import { renderWithProviders } from '../../testUtils';
import type { PremiumHistoryResponse } from '../../api/arbitrage';

const NOTE =
  "Approximation: today's holdings and senior claims are applied to past " +
  'prices, so this is not a record of the discount as it stood.';

function premiumData(overrides: Partial<PremiumHistoryResponse> = {}): PremiumHistoryResponse {
  const points = Array.from({ length: 40 }, (_, i) => ({
    date: `2026-06-${String((i % 28) + 1).padStart(2, '0')}`,
    premium_discount_pct: -10 - i * 0.2,
    nav_per_share: 108.86,
    price: 96.99,
  }));
  return {
    security: 'MSTR',
    underlying: 'BTC-USD',
    points,
    latest: points[points.length - 1],
    holdings_as_of: '2026-07-14',
    holdings_age_days: 12,
    holdings_stale: false,
    method: 'current_capital_structure',
    note: NOTE,
    ...overrides,
  };
}

describe('PremiumChart', () => {
  it('renders the heading and latest discount', () => {
    renderWithProviders(<PremiumChart data={premiumData()} />);
    expect(screen.getByText('MSTR discount to NAV')).toBeInTheDocument();
    expect(screen.getByText(/latest -17\.80%/)).toBeInTheDocument();
  });

  it('always shows the approximation caveat', () => {
    // The series is not history; a reader who misses that misreads the chart.
    renderWithProviders(<PremiumChart data={premiumData()} />);
    expect(screen.getByText(/Approximation/)).toBeInTheDocument();
    expect(screen.getByText(/not a record of the discount/)).toBeInTheDocument();
  });

  it('draws the series with a parity line at zero', () => {
    const { container } = renderWithProviders(<PremiumChart data={premiumData()} />);
    const svg = container.querySelector('[data-testid="premium-chart"]')!;
    expect(svg.querySelectorAll('line').length).toBeGreaterThanOrEqual(1);
    expect(svg.querySelectorAll('path').length).toBeGreaterThan(0);
    expect(svg.querySelectorAll('circle').length).toBe(1);
  });

  it('degrades to a message when there are no points', () => {
    renderWithProviders(<PremiumChart data={premiumData({ points: [], latest: null })} />);
    expect(screen.getByText(/No premium history for MSTR/)).toBeInTheDocument();
  });
});
