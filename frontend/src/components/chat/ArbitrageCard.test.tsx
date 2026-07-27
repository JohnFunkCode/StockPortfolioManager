import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import ArbitrageCard from './ArbitrageCard';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

/** A NAV vehicle in the MSTR shape — the case the card is designed around. */
function navPair(overrides: object = {}) {
  return {
    security: 'MSTR',
    name: 'Strategy (MicroStrategy)',
    kind: 'nav_vehicle',
    underlying: 'BTC-USD',
    as_of: '2026-07-24',
    price: 91.67,
    underlying_price: 64709,
    statistics: {
      n: 250,
      hedge_ratio: { beta: 1.44, beta_stability: 0.1, r_squared: 0.93 },
      cointegration: { cointegrated: false, reject_at: null, statistic: -2.87 },
      zscore: { z: -1.35 },
      half_life: { half_life_days: 66.4, mean_reverting: true },
      trend: { widening: false, slope_tstat: -1.0 },
      correlation: 0.73,
    },
    nav: {
      available: true,
      premium_discount_pct: -16.92,
      gross_premium_discount_pct: -41.79,
      carry_drag_pct: 2.21,
      years_of_burn: 7.6,
      exposure_ratio: 1.72,
      holdings_as_of: '2026-07-14',
      holdings_age_days: 12,
      holdings_stale: false,
    },
    hedge: { instrument: 'IBIT', available: true, futures_only: false, note: 'Hedge with IBIT.' },
    score: 9.7,
    verdict: 'reject',
    basis: 'nav_discount',
    convergence: 'buyback',
    factors: {
      opportunity: 42.3, evidence: 0.35, convergence: 0.7,
      hedge: 1.0, carry: 0.936, trend: 1.0, freshness: 1.0,
    },
    reasons: ['Carry drag of 2.21%/yr erodes the NAV while you wait'],
    breaks_on: ['Negative carry (senior claims serviced out of NAV)'],
    ...overrides,
  };
}

function mount(payload: object) {
  mockApi([['/api/arbitrage/pairs/', payload]]);
  return renderWithProviders(<ArbitrageCard ticker="MSTR" />);
}

describe('ArbitrageCard', () => {
  it('shows a loading state before the workup arrives', () => {
    mount(navPair());
    expect(screen.getByText(/Analysing MSTR/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([['/api/arbitrage/pairs/', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<ArbitrageCard ticker="MSTR" />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't analyse MSTR/i)).toBeInTheDocument(),
    );
  });

  it('surfaces an uncurated pair as guidance, not an error', async () => {
    mount({
      security: 'ZZZZ',
      error: 'ZZZZ is not in the curated universe and no underlying was supplied.',
      hint: 'Pass underlying= to analyse an ad-hoc pair.',
    });
    await waitFor(() =>
      expect(screen.getByText(/not in the curated universe/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Pass underlying=/)).toBeInTheDocument();
  });

  describe('NAV vehicle', () => {
    it('leads with the NET discount', async () => {
      mount(navPair());
      await waitFor(() => expect(screen.getByText('-16.92%')).toBeInTheDocument());
      expect(screen.getByText(/discount to net asset value/i)).toBeInTheDocument();
    });

    it('shows the gross figure only as a struck-through caveat', async () => {
      mount(navPair());
      await waitFor(() => expect(screen.getByText('-16.92%')).toBeInTheDocument());

      const gross = screen.getByText('-41.79%');
      expect(gross).toBeInTheDocument();
      // The misleading number must never occupy the headline slot.
      expect(screen.getByTestId('headline-gap').textContent).toBe('-16.92%');
      expect(getComputedStyle(gross).textDecoration).toContain('line-through');
      expect(screen.getByText(/overstates what a shareholder gets/i)).toBeInTheDocument();
    });

    it('renders the supporting metrics', async () => {
      mount(navPair());
      await waitFor(() => expect(screen.getByText('2.21%/yr')).toBeInTheDocument());
      expect(screen.getByText('1.72×')).toBeInTheDocument();
      expect(screen.getByText('66.4d')).toBeInTheDocument();
      expect(screen.getByText('IBIT')).toBeInTheDocument();
      expect(screen.getByText('no')).toBeInTheDocument(); // not cointegrated
    });

    it('decomposes the score into its factors', async () => {
      mount(navPair());
      await waitFor(() => expect(screen.getByText('Opportunity')).toBeInTheDocument());
      expect(screen.getByText('42.3')).toBeInTheDocument();
      expect(screen.getByText('×0.35')).toBeInTheDocument();
      expect(screen.getByText('×0.7')).toBeInTheDocument();
      expect(screen.getByText('×0.936')).toBeInTheDocument();
    });

    it('lists what breaks the trade and the verdict', async () => {
      mount(navPair());
      await waitFor(() => expect(screen.getByText('reject')).toBeInTheDocument());
      expect(screen.getByText(/Negative carry/i)).toBeInTheDocument();
      expect(screen.getByText('score 9.7')).toBeInTheDocument();
    });

    it('warns when the curated holdings have gone stale', async () => {
      mount(navPair({
        nav: { ...navPair().nav, holdings_stale: true, holdings_age_days: 120 },
      }));
      await waitFor(() =>
        expect(screen.getByText(/Holdings figures are 120 days old/i)).toBeInTheDocument(),
      );
    });
  });

  describe('non-NAV pair', () => {
    const producer = navPair({
      security: 'GDX',
      kind: 'producer',
      underlying: 'GC=F',
      convergence: 'none',
      nav: null,
      basis: 'spread_zscore',
      hedge: { instrument: 'GLD', available: true, futures_only: false, note: '' },
    });

    it('falls back to the spread z-score as the headline', async () => {
      mount(producer);
      await waitFor(() =>
        expect(screen.getByTestId('headline-gap').textContent).toBe('-1.35σ'),
      );
      expect(screen.getByText(/spread vs its own history/i)).toBeInTheDocument();
      expect(screen.queryByText('-16.92%')).toBeNull();
    });

    it('omits the NAV-only metric values entirely', async () => {
      mount(producer);
      await waitFor(() => expect(screen.getByTestId('headline-gap')).toBeInTheDocument());
      // The metric readouts are gone. ("Carry" survives as a score-factor
      // label, which is a different row — assert on the values, not the words.)
      expect(screen.queryByText('2.21%/yr')).toBeNull();
      expect(screen.queryByText('1.72×')).toBeNull();
      expect(screen.queryByText('Exposure')).toBeNull();
    });
  });

  it('marks an unhedgeable leg rather than naming a futures contract', async () => {
    mount(navPair({
      hedge: { instrument: null, available: false, futures_only: true, note: 'no equity hedge' },
    }));
    await waitFor(() => expect(screen.getByText('futures only')).toBeInTheDocument());
  });
});
