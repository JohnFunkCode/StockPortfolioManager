import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import WatchlistFundamentalsCard from './WatchlistFundamentalsCard';
import {
  mockApi,
  renderWithProviders,
  watchlistFundamentals,
  watchlistRow,
} from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** More rows than the card shows, so the slice is observable. */
function manyRows(n: number) {
  return Array.from({ length: n }, (_, i) =>
    watchlistRow(`SYM${i}`, { composite_score: 90 - i, return_30d: 5 - i }),
  );
}

describe('WatchlistFundamentalsCard', () => {
  it('renders the ranked table once the watchlist loads', async () => {
    mockApi([
      [
        '/api/watchlist/fundamentals',
        watchlistFundamentals([
          watchlistRow('INTC', { composite_score: 72, return_30d: 4.8, return_ytd: 12.3 }),
          watchlistRow('WMT', { composite_score: 61, return_30d: -1.5, return_ytd: 3 }),
        ]),
      ],
    ]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    await waitFor(() =>
      expect(screen.getByTestId('watchlist-fundamentals-card')).toBeInTheDocument(),
    );

    expect(screen.getByText('INTC')).toBeInTheDocument();
    expect(screen.getByText('WMT')).toBeInTheDocument();
    // Returns arrive as percent — formatPercentRaw, not formatPercent, or 4.8
    // would render as 480%.
    expect(screen.getByText('4.8%')).toBeInTheDocument();
    expect(screen.getByText('-1.5%')).toBeInTheDocument();
    expect(screen.getByText('12.3%')).toBeInTheDocument();
    expect(screen.getByText('72')).toBeInTheDocument();
  });

  it('shows a spinner while the request is in flight', () => {
    mockApi([['/api/watchlist/fundamentals', watchlistFundamentals()]]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    expect(screen.getByText(/Loading the watchlist/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([['/api/watchlist/fundamentals', { __status: 500, error: 'boom' }]]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load the watchlist/i)).toBeInTheDocument(),
    );
  });

  it('warns rather than going quiet when the watchlist is empty', async () => {
    // An empty watchlist is the condition main.py raises a Discord alarm for,
    // so the card must not render as a blank box.
    mockApi([['/api/watchlist/fundamentals', watchlistFundamentals([])]]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    await waitFor(() =>
      expect(screen.getByText(/The watchlist is empty/i)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('watchlist-fundamentals-card')).not.toBeInTheDocument();
  });

  it('slices the top ten and says how many it is out of', async () => {
    mockApi([['/api/watchlist/fundamentals', watchlistFundamentals(manyRows(14))]]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    await waitFor(() => expect(screen.getByText('SYM0')).toBeInTheDocument());

    expect(screen.getByText('Watchlist — top 10')).toBeInTheDocument();
    expect(screen.getByText('of 14')).toBeInTheDocument();
    expect(screen.getByText('SYM9')).toBeInTheDocument();
    expect(screen.queryByText('SYM10')).not.toBeInTheDocument();
  });

  it('renders the stale and unscored counts as chips', async () => {
    mockApi([
      [
        '/api/watchlist/fundamentals',
        watchlistFundamentals([
          watchlistRow('INTC', { fundamentals_stale: true }),
          watchlistRow('WMT', { composite_score: null, fundamental_label: null }),
        ]),
      ],
    ]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    await waitFor(() => expect(screen.getByText('1 stale')).toBeInTheDocument());
    expect(screen.getByText('1 unscored')).toBeInTheDocument();
  });

  it('dashes out a missing score or return rather than printing a zero', async () => {
    mockApi([
      [
        '/api/watchlist/fundamentals',
        watchlistFundamentals([
          watchlistRow('NEW', {
            composite_score: null,
            fundamental_label: null,
            return_30d: null,
            return_ytd: null,
          }),
        ]),
      ],
    ]);
    renderWithProviders(<WatchlistFundamentalsCard />);
    await waitFor(() => expect(screen.getByText('NEW')).toBeInTheDocument());

    expect(screen.getAllByText('—')).toHaveLength(3);
    expect(screen.queryByText('0')).not.toBeInTheDocument();
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument();
  });
});
