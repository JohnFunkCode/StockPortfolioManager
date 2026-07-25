import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import SymbolsPage from './SymbolsPage';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('SymbolsPage', () => {
  it('renders the symbols list', async () => {
    mockApi([
      ['/api/symbols', { symbols: [{ symbol_id: 1, symbol: 'INTC', ticker: 'INTC', name: 'Intel', currency: 'USD', bar_count: 500 }] }],
      [/\/api\//, {}],
    ]);
    renderWithProviders(<SymbolsPage />);
    await waitFor(() => expect(screen.getByText('Symbols')).toBeInTheDocument());
  });

  it('surfaces a load error', async () => {
    mockApi([['/api/symbols', () => ({ __status: 500, error: 'symbols down' })], [/\/api\//, {}]]);
    renderWithProviders(<SymbolsPage />);
    await waitFor(() => expect(screen.getByText(/symbols down/i)).toBeInTheDocument());
  });
});
