import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import CashLedger, { RECENT_ENTRIES } from './CashLedger';
import { cashEntry, cashLedger, emptyCashLedger, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const OPENING = cashEntry({
  entry_id: 1, kind: 'opening_balance', entry_date: '2026-01-01', amount: 1000, balance_after: 1000,
});
const BUY = cashEntry({
  entry_id: 3, kind: 'buy', entry_date: '2026-01-05', amount: -150, balance_after: 850,
  lot_id: 11, symbol: 'INTC', shares: 5, price: 30, currency: 'USD',
});
const SELL = cashEntry({
  entry_id: 4, kind: 'sell', entry_date: '2026-03-01', amount: 220.5, balance_after: 1070.5,
  sale_id: 21, symbol: 'SAP.DE', shares: 2.5, price: 84, currency: 'EUR',
});
const WITHDRAWAL = cashEntry({
  entry_id: 5, kind: 'withdrawal', entry_date: '2026-03-02', amount: -40, balance_after: 1030.5,
  notes: 'rent',
});

function mount(ledger: object, props: object = {}) {
  const api = mockApi([['/api/portfolio/cash', ledger]]);
  renderWithProviders(<CashLedger {...props} />);
  return api;
}

const row = (id: number) => within(screen.getByTestId(`cash-entry-${id}`));
const body = (api: { calls: Array<[string, RequestInit | undefined]> }, i: number) =>
  JSON.parse(String(api.calls[i][1]?.body));

describe('CashLedger', () => {
  it('says it is loading, and shows an error when the request fails', async () => {
    mockApi([['/api/portfolio/cash', () => new Promise<object>(() => {})]]);
    const { unmount } = renderWithProviders(<CashLedger />);
    expect(screen.getByText(/Loading cash/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Cash and orders' })).toBeInTheDocument();
    unmount();

    mockApi([['/api/portfolio/cash', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<CashLedger />);
    await waitFor(() => expect(screen.getByText(/Couldn't load cash/)).toBeInTheDocument());
  });

  it("shows the server's balance, totals and each entry's running balance", async () => {
    mount(
      cashLedger([WITHDRAWAL, SELL, BUY, OPENING], {
        balance: 1030.5, deposits: 0, withdrawals: -40, bought: -150, sold: 220.5,
      }),
    );
    await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());

    for (const [label, value] of [
      ['Cash', '$1,030.50'], ['Opening balance', '$1,000.00'], ['Deposits', '$0.00'],
      ['Withdrawals', '-$40.00'], ['Bought', '-$150.00'], ['Sold', '$220.50'],
    ]) {
      const stat = screen.getAllByText(label).find((el) => el.tagName === 'SPAN')!.parentElement!;
      expect(within(stat).getByText(value), label).toBeInTheDocument();
    }
    expect(screen.getByText(/at the start of/)).toBeInTheDocument();

    // Newest first, as sent.
    const ids = screen.getAllByTestId(/^cash-entry-/).map((el) => el.getAttribute('data-testid'));
    expect(ids).toEqual(['cash-entry-5', 'cash-entry-4', 'cash-entry-3', 'cash-entry-1']);

    expect(row(3).getByText('Buy 5 INTC @ $30.00')).toBeInTheDocument();
    expect(row(3).getByText('-$150.00')).toBeInTheDocument();
    expect(row(3).getByText('$850.00')).toBeInTheDocument();
    // A trade is described in its own currency; its amount is in dollars.
    expect(row(4).getByText('Sell 2.5 SAP.DE @ €84.00')).toBeInTheDocument();
    expect(row(4).getByText('$220.50')).toBeInTheDocument();
    expect(row(5).getByText('Withdrawal')).toBeInTheDocument();
    expect(row(5).getByText('rent')).toBeInTheDocument();
    expect(row(1).getByText('Opening balance')).toBeInTheDocument();
    expect(screen.getByText(/In US dollars, newest first. Buys and sells exclude fees./)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('warns when cash is below zero', async () => {
    mount(cashLedger([OPENING], { balance: -60, negative: true }));
    expect(await screen.findByText(/Cash is below zero/)).toBeInTheDocument();
    expect(screen.getByText('-$60.00')).toBeInTheDocument();
  });

  it('flags an entry with no exchange rate and says it is not in the balance', async () => {
    const unrated = { ...SELL, amount: null, fx_missing: true, balance_after: 850 };
    mount(cashLedger([unrated, BUY, OPENING], { fx_missing_count: 1, balance: 850 }));
    await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());

    expect(row(4).getByText('no FX rate')).toBeInTheDocument();
    expect(row(4).queryByText(/N\/A|\$0\.00/)).toBeNull();
    expect(screen.getByText(/1 entry with no exchange rate is not in the balance/)).toBeInTheDocument();
  });

  it('says how many entries predate the opening balance', async () => {
    mount(cashLedger([OPENING], { earlier_entry_count: 3 }));
    expect(
      await screen.findByText(/3 entries dated before the opening balance are not shown/),
    ).toBeInTheDocument();
  });

  it(`shows the latest ${RECENT_ENTRIES} entries until asked for all`, async () => {
    const many = Array.from({ length: 8 }, (_, i) =>
      cashEntry({ entry_id: 100 - i, entry_date: `2026-02-${String(20 - i).padStart(2, '0')}` }),
    );
    mount(cashLedger(many));
    await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());
    expect(screen.getAllByTestId(/^cash-entry-/)).toHaveLength(RECENT_ENTRIES);

    fireEvent.click(screen.getByRole('button', { name: 'Show all 8 entries' }));
    expect(screen.getAllByTestId(/^cash-entry-/)).toHaveLength(8);
    fireEvent.click(screen.getByRole('button', { name: 'Show the latest entries' }));
    expect(screen.getAllByTestId(/^cash-entry-/)).toHaveLength(RECENT_ENTRIES);
  });

  it('offers no "show all" for a short ledger', async () => {
    mount(cashLedger());
    await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull();
  });

  describe('a book with no opening balance', () => {
    it('asks the real book for one and says what is waiting', async () => {
      mount(emptyCashLedger({ earlier_entry_count: 4 }));
      expect(await screen.findByText(/no opening balance yet\. Enter the cash/)).toBeInTheDocument();
      expect(screen.getByText(/4 entries from its lots and sales will count/)).toBeInTheDocument();
      expect(screen.queryByTestId('cash-table')).toBeNull();
      expect(screen.queryByText(/\$|N\/A/)).toBeNull();
    });

    it('saves the opening balance the owner types', async () => {
      const api = mount(emptyCashLedger());
      fireEvent.click(await screen.findByRole('button', { name: 'Set opening balance' }));
      const form = within(screen.getByTestId('cash-form-opening'));
      fireEvent.change(form.getByLabelText('Date'), { target: { value: '2026-01-01' } });
      fireEvent.change(form.getByLabelText('Cash held'), { target: { value: '1000.50' } });
      fireEvent.click(form.getByRole('button', { name: 'Save opening balance' }));

      await waitFor(() => expect(api.calls.some(([, init]) => init?.method === 'PUT')).toBe(true));
      const put = api.calls.findIndex(([, init]) => init?.method === 'PUT');
      expect(api.calls[put][0]).toBe('/api/portfolio/cash/opening-balance');
      expect(body(api, put)).toEqual({ date: '2026-01-01', amount: 1000.5 });
      // The ledger is read again, and the form closes.
      await waitFor(() => expect(screen.queryByTestId('cash-form-opening')).toBeNull());
      expect(api.calls.filter(([, init]) => !init?.method).length).toBeGreaterThan(1);
    });

    it('tells a paper book only that it has none', async () => {
      mount(emptyCashLedger({ book_kind: 'paper' }), { bookId: 7 });
      expect(await screen.findByText(/no opening balance yet, so it has no cash balance/)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Set opening balance' })).toBeNull();
    });
  });

  describe('entries the owner types (real book)', () => {
    it('records a withdrawal as a positive amount with its kind', async () => {
      const api = mount(cashLedger());
      fireEvent.click(await screen.findByRole('button', { name: 'Add deposit or withdrawal' }));
      const form = within(screen.getByTestId('cash-form-entry'));
      fireEvent.mouseDown(form.getByRole('combobox'));
      fireEvent.click(await screen.findByRole('option', { name: 'Withdrawal' }));
      fireEvent.change(form.getByLabelText('Date'), { target: { value: '2026-02-03' } });
      fireEvent.change(form.getByLabelText('Amount'), { target: { value: '40' } });
      fireEvent.change(form.getByLabelText('Note'), { target: { value: '  rent  ' } });
      fireEvent.click(form.getByRole('button', { name: 'Save entry' }));

      await waitFor(() => expect(api.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
      const post = api.calls.findIndex(([, init]) => init?.method === 'POST');
      expect(api.calls[post][0]).toBe('/api/portfolio/cash/entries');
      expect(body(api, post)).toEqual({ kind: 'withdrawal', date: '2026-02-03', amount: 40, notes: 'rent' });
    });

    it('stops a blank, zero or negative amount before it goes out', async () => {
      const api = mount(cashLedger());
      fireEvent.click(await screen.findByRole('button', { name: 'Add deposit or withdrawal' }));
      const form = within(screen.getByTestId('cash-form-entry'));
      for (const value of ['', '0', '-5']) {
        fireEvent.change(form.getByLabelText('Amount'), { target: { value } });
        fireEvent.click(form.getByRole('button', { name: 'Save entry' }));
        expect(form.getByText('Enter an amount above zero.')).toBeInTheDocument();
      }
      fireEvent.change(form.getByLabelText('Amount'), { target: { value: '5' } });
      fireEvent.change(form.getByLabelText('Date'), { target: { value: '' } });
      fireEvent.click(form.getByRole('button', { name: 'Save entry' }));
      expect(form.getByText('Pick a date.')).toBeInTheDocument();
      expect(api.calls.every(([, init]) => !init?.method)).toBe(true);
    });

    it("shows the server's reason when it refuses an entry, and keeps the form", async () => {
      mockApi([
        ['/api/portfolio/cash/entries', () => ({ __status: 422, error: 'date cannot be in the future' })],
        ['/api/portfolio/cash', cashLedger()],
      ]);
      renderWithProviders(<CashLedger />);
      fireEvent.click(await screen.findByRole('button', { name: 'Add deposit or withdrawal' }));
      const form = within(screen.getByTestId('cash-form-entry'));
      fireEvent.change(form.getByLabelText('Amount'), { target: { value: '5' } });
      fireEvent.click(form.getByRole('button', { name: 'Save entry' }));
      expect(await form.findByText(/date cannot be in the future/)).toBeInTheDocument();
    });

    it('opens the opening-balance form on the saved values, and takes a negative balance', async () => {
      const api = mount(cashLedger());
      fireEvent.click(await screen.findByRole('button', { name: 'Change opening balance' }));
      const form = within(screen.getByTestId('cash-form-opening'));
      expect(form.getByLabelText('Date')).toHaveValue('2026-01-01');
      expect(form.getByLabelText('Cash held')).toHaveValue(1000);

      fireEvent.change(form.getByLabelText('Cash held'), { target: { value: '-25' } });
      fireEvent.click(form.getByRole('button', { name: 'Save opening balance' }));
      await waitFor(() => expect(api.calls.some(([, init]) => init?.method === 'PUT')).toBe(true));
      const put = api.calls.findIndex(([, init]) => init?.method === 'PUT');
      expect(body(api, put)).toEqual({ date: '2026-01-01', amount: -25 });
    });

    it('cancel closes a form without a request', async () => {
      const api = mount(cashLedger());
      fireEvent.click(await screen.findByRole('button', { name: 'Add deposit or withdrawal' }));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByTestId('cash-form-entry')).toBeNull();
      expect(api.calls).toHaveLength(1);
    });

    it('deletes a deposit, and offers no delete for a trade or the opening balance', async () => {
      const api = mount(cashLedger([WITHDRAWAL, BUY, OPENING]));
      await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());
      expect(row(3).queryByRole('button')).toBeNull();
      expect(row(1).queryByRole('button')).toBeNull();

      fireEvent.click(row(5).getByRole('button', { name: /Delete withdrawal of/ }));
      await waitFor(() => expect(api.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true));
      const del = api.calls.findIndex(([, init]) => init?.method === 'DELETE');
      expect(api.calls[del][0]).toBe('/api/portfolio/cash/entries/5');
    });
  });

  describe('a book that takes no entries', () => {
    it('shows a paper book its ledger with no forms and no delete', async () => {
      const api = mount(cashLedger([WITHDRAWAL, OPENING], { book_kind: 'paper' }), { bookId: 7 });
      await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());
      expect(api.calls[0][0]).toBe('/api/portfolio/cash?book_id=7');
      expect(screen.queryByRole('button', { name: /deposit|opening balance|Delete/i })).toBeNull();
    });

    it('is read-only when told to be, even for the real book', async () => {
      mount(cashLedger([WITHDRAWAL, OPENING]), { readOnly: true, title: 'Your cash' });
      await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());
      expect(screen.getByRole('heading', { level: 2, name: 'Your cash' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /deposit|opening balance|Delete/i })).toBeNull();
    });
  });
});
