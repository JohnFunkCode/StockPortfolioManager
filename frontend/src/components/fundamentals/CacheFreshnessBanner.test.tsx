import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import CacheFreshnessBanner from './CacheFreshnessBanner';
import { fundamentalsCacheStats, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const STATS = '/api/securities/fundamentals/cache-stats';

describe('CacheFreshnessBanner', () => {
  it('shows a skeleton while the request is in flight', () => {
    mockApi([[STATS, fundamentalsCacheStats()]]);
    const { container } = renderWithProviders(<CacheFreshnessBanner />);
    expect(container.querySelector('.MuiSkeleton-root')).not.toBeNull();
  });

  it('renders a chip per data type with its symbol count', async () => {
    mockApi([
      [
        STATS,
        fundamentalsCacheStats({
          data_types: [
            {
              data_type: 'income_statement',
              symbol_count: 41,
              oldest: '2026-08-01T06:00:00Z',
              newest: '2026-08-09T06:00:00Z',
            },
            {
              data_type: 'balance_sheet',
              symbol_count: 38,
              oldest: '2026-07-30T06:00:00Z',
              newest: '2026-08-09T06:00:00Z',
            },
          ],
        }),
      ],
    ]);
    renderWithProviders(<CacheFreshnessBanner />);
    await waitFor(() => expect(screen.getByTestId('cache-freshness-banner')).toBeInTheDocument());

    // Underscores are cosmetic here — the chip reads as prose.
    expect(screen.getByText('income statement · 41')).toBeInTheDocument();
    expect(screen.getByText('balance sheet · 38')).toBeInTheDocument();
    expect(screen.getByTestId('cache-chip-income_statement')).toBeInTheDocument();
  });

  it('never paints the database identifier the response carries', async () => {
    // The route names the database host:port/name (never a DSN); the banner
    // still has no reason to put it on screen.
    mockApi([[STATS, fundamentalsCacheStats()]]);
    renderWithProviders(<CacheFreshnessBanner />);
    await waitFor(() => expect(screen.getByTestId('cache-freshness-banner')).toBeInTheDocument());
    expect(screen.queryByText(/quantcore_test/)).not.toBeInTheDocument();
  });

  it('disappears on a failed fetch rather than taking the page down', async () => {
    mockApi([[STATS, { __status: 500, error: 'boom' }]]);
    const { container } = renderWithProviders(<CacheFreshnessBanner />);
    await waitFor(() => expect(container.querySelector('.MuiSkeleton-root')).toBeNull());
    expect(screen.queryByTestId('cache-freshness-banner')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('disappears when the server reports an error in a 200 body', async () => {
    mockApi([[STATS, fundamentalsCacheStats({ error: 'cache unavailable' })]]);
    renderWithProviders(<CacheFreshnessBanner />);
    await waitFor(() =>
      expect(screen.queryByTestId('cache-freshness-banner')).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/cache unavailable/)).not.toBeInTheDocument();
  });

  it('renders nothing when the cache is empty', async () => {
    mockApi([[STATS, fundamentalsCacheStats({ data_types: [] })]]);
    const { container } = renderWithProviders(<CacheFreshnessBanner />);
    await waitFor(() => expect(container.querySelector('.MuiSkeleton-root')).toBeNull());
    expect(screen.queryByTestId('cache-freshness-banner')).not.toBeInTheDocument();
  });
});
