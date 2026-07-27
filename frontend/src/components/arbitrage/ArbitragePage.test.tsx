import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import ArbitragePage from './ArbitragePage';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const SCAN = {
  scanned: 2,
  returned: 2,
  candidates: [
    {
      security: 'MSTR', name: 'Strategy', kind: 'nav_vehicle', underlying: 'BTC-USD',
      score: 9.7, verdict: 'reject', basis: 'nav_discount', discount_pct: -16.92,
      spread_zscore: -1.35, half_life_days: 66.4, cointegrated: false,
      convergence: 'buyback', hedge_instrument: 'IBIT', hedge_available: true,
      breaks_on: ['Negative carry'],
    },
    {
      security: 'GDX', name: 'Gold Miners', kind: 'producer', underlying: 'GC=F',
      score: 2.6, verdict: 'reject', basis: 'spread_zscore', discount_pct: null,
      spread_zscore: -0.35, half_life_days: 12.0, cointegrated: false,
      convergence: 'none', hedge_instrument: 'GLD', hedge_available: true,
      breaks_on: [],
    },
  ],
  errors: [],
};

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

const PREMIUM = {
  security: 'MSTR', underlying: 'BTC-USD',
  points: [{ date: '2026-05-01', premium_discount_pct: -12, nav_per_share: 110, price: 96 }],
  latest: { date: '2026-05-01', premium_discount_pct: -12, nav_per_share: 110, price: 96 },
  method: 'current_capital_structure',
  note: 'Approximation: today’s holdings applied to past prices.',
};

function arm(extra: Array<[string, unknown]> = []) {
  return mockApi([
    ...(extra as Array<[string, never]>),
    ['/spread-history', SPREAD],
    ['/premium-history', PREMIUM],
    ['/api/arbitrage/scan', SCAN],
    ['/api/arbitrage/pairs/', { security: 'MSTR', error: 'stub' }],
    ['/api/arbitrage/discover', { count: 0, pairs: [], skipped: [], references: [], tested: [] }],
  ]);
}

describe('ArbitragePage', () => {
  it('explains the wait while the scan runs', () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    expect(screen.getByText(/Scanning the curated universe/i)).toBeInTheDocument();
  });

  it('renders the ranked rows once the scan lands', async () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText('MSTR')).toBeInTheDocument());
    expect(screen.getByText('GDX')).toBeInTheDocument();
    expect(screen.getByText('2 of 2 pairs')).toBeInTheDocument();
  });

  it('surfaces a scan failure', async () => {
    mockApi([['/api/arbitrage/scan', () => ({ __status: 500, error: 'scan blew up' })]]);
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText(/scan blew up/i)).toBeInTheDocument());
  });

  it('flags pairs that errored during the scan', async () => {
    arm([['/api/arbitrage/scan', { ...SCAN, errors: [{ security: 'BOOM', error: 'x' }] }]]);
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText('1 errored')).toBeInTheDocument());
  });

  it('opens the pair detail with a spread chart on row click', async () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText('MSTR')).toBeInTheDocument());
    fireEvent.click(screen.getByText('MSTR'));
    await waitFor(() =>
      expect(screen.getByTestId('spread-chart')).toBeInTheDocument(),
    );
  });

  it('shows the NAV premium chart only for nav_vehicle rows', async () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText('GDX')).toBeInTheDocument());

    fireEvent.click(screen.getByText('GDX'));
    await waitFor(() => expect(screen.getByTestId('spread-chart')).toBeInTheDocument());
    // GDX is a producer — no net asset value to discount against.
    expect(screen.queryByTestId('premium-chart')).toBeNull();

    fireEvent.click(screen.getByText('MSTR'));
    await waitFor(() => expect(screen.getByTestId('premium-chart')).toBeInTheDocument());
  });

  it('runs a discovery sweep on demand', async () => {
    const api = arm();
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText('MSTR')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Discovery/i }));
    const input = screen.getByLabelText(/Symbols/i);
    fireEvent.change(input, { target: { value: 'NEM, AEM' } });
    fireEvent.click(screen.getByRole('button', { name: /^Sweep$/i }));

    await waitFor(() =>
      expect(api.calls.some(([url]) => url.includes('include_all=true'))).toBe(true),
    );
  });

  it('keeps Sweep disabled until symbols are entered', async () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    fireEvent.click(screen.getByRole('button', { name: /Discovery/i }));
    expect(screen.getByRole('button', { name: /^Sweep$/i })).toBeDisabled();
  });
});
