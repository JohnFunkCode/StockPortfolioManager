import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import CashLedgerCard from './CashLedgerCard';
import { cashLedger, emptyCashLedger, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('CashLedgerCard', () => {
  it("shows the caller's cash for the real book, with no way to change it", async () => {
    const api = mockApi([['/api/portfolio/cash', cashLedger()]]);
    renderWithProviders(<CashLedgerCard />);
    await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());

    expect(screen.getByTestId('cash-ledger-card')).toBeInTheDocument();
    expect(screen.getByText('Your cash')).toBeInTheDocument();
    expect(screen.getAllByText('$1,250.00').length).toBeGreaterThan(0);
    // Read-only: no forms and no delete, though the book is the real one.
    expect(screen.queryByRole('button', { name: /deposit|opening balance|Delete/i })).toBeNull();
    // No book is ever named: the card has no props to name one with.
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0][0]).toBe('/api/portfolio/cash');
  });

  it('says it is loading while the request is in flight', () => {
    mockApi([['/api/portfolio/cash', () => new Promise<object>(() => {})]]);
    renderWithProviders(<CashLedgerCard />);
    expect(screen.getByText(/Loading cash/)).toBeInTheDocument();
  });

  it('shows an error when the request fails', async () => {
    mockApi([['/api/portfolio/cash', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<CashLedgerCard />);
    await waitFor(() => expect(screen.getByText(/Couldn't load cash/)).toBeInTheDocument());
  });

  it('explains a book with no opening balance, and offers no form for one', async () => {
    mockApi([['/api/portfolio/cash', emptyCashLedger()]]);
    renderWithProviders(<CashLedgerCard />);
    await waitFor(() => expect(screen.getByText(/no opening balance yet/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Set opening balance' })).toBeNull();
  });
});
