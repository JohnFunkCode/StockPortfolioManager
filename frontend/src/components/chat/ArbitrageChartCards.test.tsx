import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import {
  SpreadHistoryCard,
  PremiumHistoryCard,
  ArbScanCard,
  DiscoveryCard,
} from './ArbitrageChartCards';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const SPREAD = {
  security: 'MSTR', kind: 'nav_vehicle', underlying: 'BTC-USD',
  points: [{ date: '2026-05-01', spread: 0.1 }, { date: '2026-05-02', spread: -0.1 }],
  mean: 0, std: 0.12,
  bands: { plus_one: 0.12, minus_one: -0.12, plus_two: 0.24, minus_two: -0.24 },
  latest: { date: '2026-05-02', spread: -0.1, z: -0.8 },
  hedge_ratio: { beta: 1.4, alpha: 0.1 },
  half_life: { half_life_days: 20 },
  cointegration: { cointegrated: false, statistic: -2.8 },
  trend: { slope_per_day: -0.001, intercept: 0.05, slope_tstat: -1, widening: false },
};

describe('SpreadHistoryCard', () => {
  it('fetches and renders the chart from a ticker alone', async () => {
    mockApi([['/spread-history', SPREAD]]);
    renderWithProviders(<SpreadHistoryCard ticker="MSTR" />);
    expect(screen.getByText(/Loading MSTR spread history/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('spread-chart')).toBeInTheDocument());
  });

  it('shows an error alert when the request fails', async () => {
    mockApi([['/spread-history', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<SpreadHistoryCard ticker="MSTR" />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load MSTR spread history/i)).toBeInTheDocument(),
    );
  });

  it('passes a structured service refusal through as guidance', async () => {
    mockApi([['/spread-history', { security: 'ZZ', error: 'ZZ is not in the curated universe.' }]]);
    renderWithProviders(<SpreadHistoryCard ticker="ZZ" />);
    await waitFor(() =>
      expect(screen.getByText(/not in the curated universe/i)).toBeInTheDocument(),
    );
  });
});

describe('PremiumHistoryCard', () => {
  it('renders the premium chart with its caveat', async () => {
    mockApi([
      ['/premium-history', {
        security: 'MSTR', underlying: 'BTC-USD',
        points: [{ date: '2026-05-01', premium_discount_pct: -12, nav_per_share: 110, price: 96 }],
        latest: { date: '2026-05-01', premium_discount_pct: -12, nav_per_share: 110, price: 96 },
        method: 'current_capital_structure',
        note: 'Approximation: today’s holdings applied to past prices.',
      }],
    ]);
    renderWithProviders(<PremiumHistoryCard ticker="MSTR" />);
    await waitFor(() => expect(screen.getByTestId('premium-chart')).toBeInTheDocument());
    expect(screen.getByText(/Approximation/)).toBeInTheDocument();
  });

  it('explains why a non-NAV security has no premium chart', async () => {
    // Routine for producers/ETFs — the service reason beats a generic failure.
    mockApi([['/premium-history', {
      security: 'GDX', kind: 'producer',
      error: 'GDX is a producer, not a NAV vehicle — there is no net asset value.',
    }]]);
    renderWithProviders(<PremiumHistoryCard ticker="GDX" />);
    await waitFor(() =>
      expect(screen.getByText(/not a NAV vehicle/i)).toBeInTheDocument(),
    );
  });
});

describe('ArbScanCard', () => {
  it('renders the scan table', async () => {
    mockApi([['/api/arbitrage/scan', {
      scanned: 1, returned: 1, errors: [],
      candidates: [{
        security: 'MSTR', kind: 'nav_vehicle', underlying: 'BTC-USD', score: 9.7,
        verdict: 'reject', basis: 'nav_discount', discount_pct: -16.92,
        spread_zscore: -1.35, half_life_days: 66.4, cointegrated: false,
        convergence: 'buyback', hedge_instrument: 'IBIT', hedge_available: true,
        breaks_on: [],
      }],
    }]]);
    renderWithProviders(<ArbScanCard />);
    expect(screen.getByText(/Scanning the curated universe/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('scan-table')).toBeInTheDocument());
    expect(screen.getByText('MSTR')).toBeInTheDocument();
  });
});

describe('DiscoveryCard', () => {
  it('plots the tested pairs from a symbols string', async () => {
    mockApi([['/api/arbitrage/discover', {
      count: 0, pairs: [], skipped: [], references: ['GC=F'],
      tested: [{
        security: 'NEM', underlying: 'GC=F', economic_link: true, correlation: 0.73,
        statistic: -2.87, reject_at: null, half_life_days: 6.4, passed: false,
        failed_because: 'not cointegrated',
      }],
      critical_values: { '0.05': -3.34 },
    }]]);
    renderWithProviders(<DiscoveryCard symbols="NEM" />);
    await waitFor(() => expect(screen.getByTestId('discovery-scatter')).toBeInTheDocument());
    expect(screen.getByText(/0 of 1 pairs passed/)).toBeInTheDocument();
  });
});
