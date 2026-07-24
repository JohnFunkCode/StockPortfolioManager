import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import SecurityDetailPage from './SecurityDetailPage';
import {
  indicatorRows,
  ivExpirations,
  mockApi,
  optionsSnapshot,
  pcHistoryRows,
  renderWithProviders,
  securityRow,
} from '../../testUtils';

/** Every endpoint the detail page and its tabs may touch. */
function armAll(overrides: Record<string, unknown> = {}) {
  return mockApi([
    ['/technicals', overrides.tech ?? { ticker: 'INTC', indicators: indicatorRows(60) }],
    ['/options/latest', { ticker: 'INTC', snapshot: optionsSnapshot() }],
    ['/options/history', { ticker: 'INTC', history: pcHistoryRows(30) }],
    ['/options/analytics', {
      ticker: 'INTC',
      price: 102,
      analytics: [{
        expiration: '2026-08-21', max_pain: 100, expected_move_dollar: 5,
        expected_move_pct: 5, atm_strike: 100, upper_bound: 107, lower_bound: 97,
        total_call_oi: 1, total_put_oi: 1, put_call_ratio: 1,
        pain_curve: [{ strike: 100, pain: 1000 }],
      }],
    }],
    ['/options/iv-rank', { ticker: 'INTC', current_iv: 45, iv_rank: 50, iv_percentile: 55, iv_52w_high: 80, iv_52w_low: 20, data_points: 100, history: ivExpirations() }],
    ['/earnings', { ticker: 'INTC', earnings_date: '2026-08-01', days_to_earnings: 30 }],
    ['/api/securities', { securities: [securityRow('INTC', { source: 'portfolio' })] }],
    // Signals tab endpoints + LivePrice + catch-all.
    ['/signals/technical', { ticker: 'INTC', _errors: null }],
    ['/signals/options-flow', { ticker: 'INTC', _errors: null }],
    ['/signals/risk', { ticker: 'INTC', drawdown: null }],
    ['/news', { symbol: 'INTC', article_count: 0, articles: [] }],
    [/\/api\//, {}],
  ]);
}

function renderPage() {
  return renderWithProviders(<SecurityDetailPage />, {
    route: '/securities/INTC',
    path: '/securities/:symbol',
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('SecurityDetailPage', () => {
  it('renders the ticker header and tab bar', async () => {
    armAll();
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText('INTC').length).toBeGreaterThan(0),
    );
    expect(screen.getByText('Price & MAs')).toBeInTheDocument();
    expect(screen.getByText('Options Chain')).toBeInTheDocument();
    expect(screen.getByText('Signals')).toBeInTheDocument();
  });

  it('navigates across tabs without crashing', async () => {
    armAll();
    renderPage();
    await waitFor(() => expect(screen.getByText('Options Chain')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Options Chain'));
    fireEvent.click(screen.getByText('Options Analytics'));
    fireEvent.click(screen.getByText('Signals'));
    // Signals tab renders its own section header.
    await waitFor(() =>
      expect(screen.getByText('Momentum & Volume')).toBeInTheDocument(),
    );
  });

  it('renders every tab panel, exercising the interpretation builders', async () => {
    armAll();
    renderPage();
    await waitFor(() => expect(screen.getByText('Technical Analysis')).toBeInTheDocument());
    // Walk all six tabs; each panel body + its narrative helpers run.
    for (const label of [
      'Technical Analysis',
      'Options Chain',
      'Options Performance',
      'Options Analytics',
      'Signals',
      'Price & MAs',
    ]) {
      // Click the tab (role=tab disambiguates from panel text like "Options Chain").
      fireEvent.click(screen.getByRole('tab', { name: label }));
      await waitFor(() =>
        expect(screen.getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'true'),
      );
    }
  });

  it('opens the remove-from-portfolio confirmation', async () => {
    armAll();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Remove from Portfolio')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Remove from Portfolio'));
    // The confirmation dialog surfaces the archival copy.
    await waitFor(() =>
      expect(screen.getAllByText(/Remove from Portfolio/i).length).toBeGreaterThan(1),
    );
  });

  it('offers a Remove from Portfolio action for a held name', async () => {
    armAll();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Remove from Portfolio')).toBeInTheDocument(),
    );
  });

  it('still renders the page when technicals fail', async () => {
    armAll({ tech: { __status: 500, error: 'technicals down' } });
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText('INTC').length).toBeGreaterThan(0),
    );
    // The header + tab bar survive a failed technicals query (resilience);
    // the error surfaces inline rather than blanking the page.
    expect(screen.getByText('Price & MAs')).toBeInTheDocument();
  });
});
