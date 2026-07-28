import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import SymbolLotsCard from './SymbolLotsCard';
import { lotRow, mockApi, portfolioTotals, renderWithProviders, symbolRow } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SymbolLotsCard', () => {
  it('renders only the requested ticker\'s lots', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        {
          symbols: [
            symbolRow({ symbol: 'INTC', lots: [lotRow({ lot_id: 1 }), lotRow({ lot_id: 2 })] }),
            symbolRow({ symbol: 'MSFT' }),
          ],
          totals: portfolioTotals(),
        },
      ],
    ]);
    renderWithProviders(<SymbolLotsCard ticker="INTC" />);
    await waitFor(() => expect(screen.getByTestId('symbol-lots-card')).toBeInTheDocument());
    expect(screen.getByText('INTC — Lots')).toBeInTheDocument();
    expect(screen.queryByText('MSFT')).not.toBeInTheDocument();
  });

  it('shows an info alert when the symbol has no lots', async () => {
    mockApi([
      ['/api/portfolio/symbols', { symbols: [symbolRow({ symbol: 'MSFT' })], totals: portfolioTotals() }],
    ]);
    renderWithProviders(<SymbolLotsCard ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText(/No INTC lots in your portfolio/i)).toBeInTheDocument(),
    );
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([['/api/portfolio/symbols', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<SymbolLotsCard ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load lots for INTC/i)).toBeInTheDocument(),
    );
  });
});
