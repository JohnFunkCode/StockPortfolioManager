import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import SpreadChart from './SpreadChart';
import { renderWithProviders } from '../../testUtils';
import type { SpreadHistoryResponse } from '../../api/arbitrage';

function spreadData(overrides: Partial<SpreadHistoryResponse> = {}): SpreadHistoryResponse {
  const points = Array.from({ length: 60 }, (_, i) => ({
    date: `2026-05-${String((i % 28) + 1).padStart(2, '0')}`,
    spread: Math.sin(i / 6) * 0.1,
  }));
  return {
    security: 'MSTR',
    kind: 'nav_vehicle',
    underlying: 'BTC-USD',
    points,
    mean: 0,
    std: 0.12,
    bands: { plus_one: 0.12, minus_one: -0.12, plus_two: 0.24, minus_two: -0.24 },
    latest: { date: '2026-05-28', spread: -0.1686, z: -1.35 },
    hedge_ratio: { beta: 1.44, alpha: 0.1 },
    half_life: { half_life_days: 66.4 },
    cointegration: { cointegrated: false, statistic: -2.87 },
    trend: { slope_per_day: -0.0001, intercept: 0.013, slope_tstat: -1.0, widening: false },
    ...overrides,
  };
}

describe('SpreadChart', () => {
  it('renders the pair heading and the latest z-score', () => {
    renderWithProviders(<SpreadChart data={spreadData()} />);
    expect(screen.getByText('MSTR − BTC-USD spread')).toBeInTheDocument();
    expect(screen.getByText(/latest -1\.35σ/)).toBeInTheDocument();
  });

  it('draws the series, bands and trend line', () => {
    const { container } = renderWithProviders(<SpreadChart data={spreadData()} />);
    const svg = container.querySelector('[data-testid="spread-chart"]')!;
    // Scoped by class: d3 axes emit their own <line> elements for tick marks.
    expect(svg.querySelectorAll('line.level-line')).toHaveLength(3); // ±2σ, mean
    expect(svg.querySelectorAll('line.trend-line')).toHaveLength(1);
    expect(svg.querySelectorAll('path').length).toBeGreaterThan(0);
    // Latest point marker.
    expect(svg.querySelectorAll('circle').length).toBe(1);
    expect(svg.textContent).toContain('+2σ');
    expect(svg.textContent).toContain('mean');
  });

  it('reports half-life when the spread reverts', () => {
    renderWithProviders(<SpreadChart data={spreadData()} />);
    expect(screen.getByText(/half-life 66\.4d/)).toBeInTheDocument();
  });

  it('flags a widening spread in the caption', () => {
    renderWithProviders(
      <SpreadChart
        data={spreadData({
          trend: { slope_per_day: -0.01, intercept: 0.2, slope_tstat: -9, widening: true },
        })}
      />,
    );
    expect(screen.getByText(/widening/)).toBeInTheDocument();
  });

  it('degrades to a message when there are no points', () => {
    renderWithProviders(<SpreadChart data={spreadData({ points: [] })} />);
    expect(screen.getByText(/No spread history for MSTR/)).toBeInTheDocument();
  });

  it('omits the trend line when the fit produced no intercept', () => {
    const { container } = renderWithProviders(
      <SpreadChart
        data={spreadData({
          trend: { slope_per_day: null, intercept: null, slope_tstat: null, widening: null },
        })}
      />,
    );
    const svg = container.querySelector('[data-testid="spread-chart"]')!;
    expect(svg.querySelectorAll('line.trend-line')).toHaveLength(0);
    expect(svg.querySelectorAll('line.level-line')).toHaveLength(3);
  });
});
