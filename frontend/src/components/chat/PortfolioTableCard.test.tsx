import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import PortfolioTableCard from './PortfolioTableCard';
import { mockApi, portfolioTotals, renderWithProviders, symbolRow } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PortfolioTableCard', () => {
  it('renders symbol rows and totals once the portfolio loads', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        { symbols: [symbolRow(), symbolRow({ symbol: 'MSFT' })], totals: portfolioTotals() },
      ],
    ]);
    renderWithProviders(<PortfolioTableCard />);
    await waitFor(() => expect(screen.getByTestId('portfolio-table-card')).toBeInTheDocument());
    expect(screen.getByText('INTC')).toBeInTheDocument();
    expect(screen.getByText('MSFT')).toBeInTheDocument();
  });

  it('shows an info alert when the portfolio is empty', async () => {
    mockApi([['/api/portfolio/symbols', { symbols: [], totals: null }]]);
    renderWithProviders(<PortfolioTableCard />);
    await waitFor(() =>
      expect(screen.getByText(/portfolio is empty/i)).toBeInTheDocument(),
    );
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([['/api/portfolio/symbols', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<PortfolioTableCard />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load your portfolio/i)).toBeInTheDocument(),
    );
  });
});
