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

const UNIVERSE = {
  count: 2,
  entries: [
    {
      security: 'MSTR', name: 'Strategy', kind: 'nav_vehicle', underlying: 'BTC-USD',
      hedge_instrument: 'IBIT', convergence_mechanism: 'buyback', holdings_units: 843775,
      holdings_as_of: '2026-07-14', senior_claims_usd: 16_500_000_000,
      annual_senior_cost_usd: 854_000_000, other_assets_usd: 0, diluted_shares: 350_000_000,
      source: '10-Q Q1 FY2026', notes: 'Converts plus four preferred series.',
      holdings_age_days: 122, holdings_stale: true, hedge_available: true,
    },
    {
      security: 'UNG', name: 'United States Natural Gas Fund', kind: 'commodity_etf',
      underlying: 'NG=F', hedge_instrument: null, convergence_mechanism: 'redemption',
      holdings_units: null, holdings_as_of: null, senior_claims_usd: 0,
      annual_senior_cost_usd: 0, other_assets_usd: 0, diluted_shares: null, source: null,
      notes: 'Contango decay.', holdings_age_days: null, holdings_stale: false,
      hedge_available: false,
    },
  ],
};

function arm(extra: Array<[string, unknown]> = []) {
  return mockApi([
    ...(extra as Array<[string, never]>),
    ['/spread-history', SPREAD],
    ['/premium-history', PREMIUM],
    ['/api/arbitrage/scan', SCAN],
    ['/api/arbitrage/universe', UNIVERSE],
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

  it('names each errored pair and why it failed', async () => {
    arm([['/api/arbitrage/scan', {
      ...SCAN,
      errors: [{ security: 'UNG', error: 'No price history for NG=F' }],
    }]]);
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByTestId('scan-errors')).toBeInTheDocument());
    expect(screen.getByTestId('scan-errors')).toHaveTextContent('UNG: No price history for NG=F');
  });

  it('shows no error list when every pair scored', async () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    await waitFor(() => expect(screen.getByText('2 of 2 pairs')).toBeInTheDocument());
    expect(screen.queryByTestId('scan-errors')).toBeNull();
  });

  it('opens on the Scan tab and counts the universe on the other', async () => {
    arm();
    renderWithProviders(<ArbitragePage />);
    expect(screen.getByRole('tab', { name: 'Scan' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Universe · 2' })).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('universe-table')).toBeNull();
  });

  it('lists the curated universe while the scan is still running', async () => {
    mockApi([
      ['/api/arbitrage/scan', () => new Promise<object>(() => {})],
      ['/api/arbitrage/universe', UNIVERSE],
    ]);
    renderWithProviders(<ArbitragePage />);
    fireEvent.click(screen.getByRole('tab', { name: /Universe/ }));
    await waitFor(() => expect(screen.getByTestId('universe-table')).toBeInTheDocument());
    expect(screen.getByText('UNG')).toBeInTheDocument();
    expect(screen.getByText('122 days old')).toBeInTheDocument();
    // The scan's own wait message belongs to the Scan tab.
    expect(screen.queryByText(/Scanning the curated universe/i)).toBeNull();
  });

  it('lists the curated universe when the scan failed', async () => {
    mockApi([
      ['/api/arbitrage/scan', () => ({ __status: 500, error: 'scan blew up' })],
      ['/api/arbitrage/universe', UNIVERSE],
    ]);
    renderWithProviders(<ArbitragePage />);
    fireEvent.click(screen.getByRole('tab', { name: /Universe/ }));
    await waitFor(() => expect(screen.getByText('MSTR')).toBeInTheDocument());
    expect(screen.queryByText(/scan blew up/i)).toBeNull();
  });

  it('surfaces a universe failure on its own tab', async () => {
    mockApi([
      ['/api/arbitrage/scan', SCAN],
      ['/api/arbitrage/universe', () => ({ __status: 500, error: 'universe blew up' })],
    ]);
    renderWithProviders(<ArbitragePage />);
    expect(screen.getByRole('tab', { name: 'Universe' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Universe' }));
    await waitFor(() => expect(screen.getByText(/universe blew up/i)).toBeInTheDocument());
  });

  it('opens the workup for a pair the scan never returned', async () => {
    // UNG is in the universe but not among the scan's rows.
    arm();
    renderWithProviders(<ArbitragePage />);
    fireEvent.click(screen.getByRole('tab', { name: /Universe/ }));
    await waitFor(() => expect(screen.getByText('UNG')).toBeInTheDocument());
    fireEvent.click(screen.getByText('UNG'));
    fireEvent.click(screen.getByRole('button', { name: /Open workup/ }));
    await waitFor(() => expect(screen.getByTestId('spread-chart')).toBeInTheDocument());
    // A commodity ETF has no NAV premium chart.
    expect(screen.queryByTestId('premium-chart')).toBeNull();
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
