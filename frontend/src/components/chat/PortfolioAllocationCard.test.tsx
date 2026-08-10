import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import PortfolioAllocationCard from './PortfolioAllocationCard';
import { mockApi, portfolioTotals, renderWithProviders, symbolRow } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PortfolioAllocationCard', () => {
  it('renders the chart once the portfolio loads', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        { symbols: [symbolRow(), symbolRow({ symbol: 'MSFT' })], totals: portfolioTotals() },
      ],
    ]);
    renderWithProviders(<PortfolioAllocationCard />);
    await waitFor(() =>
      expect(screen.getByTestId('portfolio-allocation-card')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('portfolio-allocation-chart')).toBeInTheDocument();
  });

  it('shows a spinner while the portfolio is in flight', () => {
    mockApi([['/api/portfolio/symbols', { symbols: [], totals: null }]]);
    renderWithProviders(<PortfolioAllocationCard />);
    expect(screen.getByText(/Loading your portfolio/i)).toBeInTheDocument();
  });

  it('shows an info alert when the portfolio is empty', async () => {
    mockApi([['/api/portfolio/symbols', { symbols: [], totals: null }]]);
    renderWithProviders(<PortfolioAllocationCard />);
    await waitFor(() => expect(screen.getByText(/portfolio is empty/i)).toBeInTheDocument());
  });

  it('says so when every position is unpriced rather than drawing nothing', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        {
          symbols: [symbolRow({ bar_base: null, bar_gain: null, bar_loss: null })],
          totals: portfolioTotals(),
        },
      ],
    ]);
    renderWithProviders(<PortfolioAllocationCard />);
    await waitFor(() =>
      expect(screen.getByText(/No priced positions to chart yet/i)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('portfolio-allocation-chart')).not.toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([['/api/portfolio/symbols', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<PortfolioAllocationCard />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load your portfolio/i)).toBeInTheDocument(),
    );
  });
});
