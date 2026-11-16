import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import OrdersList, { RECENT_ORDERS } from './OrdersList';
import { mockApi, order, ordersResponse, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const FILLED = order();
const DOLLAR_BUY = order({
  order_id: 13, requested_shares: null, requested_amount: 500, filled_shares: 4.050223, fill_price: 123.45,
});
const PENDING = order({
  order_id: 14, side: 'sell', requested_shares: 1.5, status: 'pending', session_date: '2026-06-16',
  resolved_at: null, filled_shares: null, fill_price: null, price_source: null, quote_time: null,
});
const REJECTED = order({
  order_id: 15, status: 'rejected', reject_reason: 'insufficient_cash',
  filled_shares: null, fill_price: null, price_source: null, quote_time: null,
});
const CANCELLED = order({
  order_id: 16, status: 'cancelled', filled_shares: null, fill_price: null, price_source: null,
});
const AT_THE_OPEN = order({ order_id: 17, price_source: 'session_open', fill_price: 49.5 });

function mount(response: object, props: object = {}) {
  const api = mockApi([
    [/\/orders\/\d+\/cancel/, { ...PENDING, status: 'cancelled' }],
    ['/api/portfolio/orders', response],
  ]);
  renderWithProviders(<OrdersList bookId={7} {...props} />);
  return api;
}

const row = (id: number) => within(screen.getByTestId(`order-${id}`));

describe('OrdersList', () => {
  it('says it is loading, and shows an error when the request fails', async () => {
    mockApi([['/api/portfolio/orders', () => new Promise<object>(() => {})]]);
    const { unmount } = renderWithProviders(<OrdersList bookId={7} />);
    expect(screen.getByText(/Loading orders/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: 'Orders' })).toBeInTheDocument();
    unmount();

    mockApi([['/api/portfolio/orders', { __status: 404, error: 'book 7 not found' }]]);
    renderWithProviders(<OrdersList bookId={7} />);
    expect(await screen.findByText(/Couldn't load orders: book 7 not found/)).toBeInTheDocument();
  });

  it('asks for the one book and says when it has no orders', async () => {
    const api = mount(ordersResponse([]));
    expect(await screen.findByText('No orders in this book yet.')).toBeInTheDocument();
    expect(api.calls.map(([url]) => url)).toEqual(['/api/portfolio/orders?book_id=7']);
    expect(screen.queryByTestId('orders-table')).toBeNull();
  });

  it('shows what each order asked for and what became of it', async () => {
    mount(ordersResponse([AT_THE_OPEN, CANCELLED, REJECTED, PENDING, DOLLAR_BUY, FILLED], { pending_count: 1 }));
    await waitFor(() => expect(screen.getByTestId('orders-table')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Show all 6 orders' }));

    expect(row(12).getByText('Buy 2 INTC')).toBeInTheDocument();
    expect(row(12).getByText('Filled')).toBeInTheDocument();
    expect(row(12).getByText('2 @ $50.00')).toBeInTheDocument();
    expect(row(12).getByText('Jun 15, 2026')).toBeInTheDocument();

    expect(row(13).getByText('Buy $500.00 of INTC')).toBeInTheDocument();
    expect(row(13).getByText('4.050223 @ $123.45')).toBeInTheDocument();

    expect(row(14).getByText('Sell 1.5 INTC')).toBeInTheDocument();
    expect(row(14).getByText('Pending')).toBeInTheDocument();
    expect(row(14).getByText('Waiting for the session of Jun 16, 2026')).toBeInTheDocument();

    expect(row(15).getByText('Rejected')).toBeInTheDocument();
    expect(row(15).getByText('not enough cash')).toBeInTheDocument();

    expect(row(16).getByText('Cancelled')).toBeInTheDocument();
    expect(row(16).getByText('Withdrawn')).toBeInTheDocument();
    expect(row(17).getByText('2 @ $49.50, the opening price')).toBeInTheDocument();

    const waiting = screen.getByText(/1 order placed outside market hours is waiting for its session/);
    // The daily job fills a queued order after the close (plan step 3.4).
    expect(waiting).toHaveTextContent(/Each fills at that session's opening price/);
    expect(waiting).toHaveTextContent(/shows here that evening, after the close/);
    expect(waiting).toHaveTextContent(/Until then it can be cancelled/);
  });

  it('puts every rejection reason into words, and shows a code it does not know', async () => {
    const reasons = ['stale_quote', 'no_quote', 'non_usd', 'insufficient_shares', 'no_open_price', 'new_reason'];
    mount(ordersResponse(reasons.map((reject_reason, i) => ({ ...REJECTED, order_id: 100 + i, reject_reason }))));
    await waitFor(() => expect(screen.getByTestId('orders-table')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Show all 6 orders' }));

    expect(row(100).getByText('no recent trade to price it from')).toBeInTheDocument();
    expect(row(101).getByText('no quote for this symbol')).toBeInTheDocument();
    expect(row(102).getByText('not a US-dollar symbol')).toBeInTheDocument();
    expect(row(103).getByText('not enough shares')).toBeInTheDocument();
    expect(row(104).getByText('no opening price that session')).toBeInTheDocument();
    expect(row(105).getByText('new_reason')).toBeInTheDocument();
  });

  it('draws each status on its tint with the matching ink', async () => {
    mount(ordersResponse([PENDING, FILLED]));
    await waitFor(() => expect(screen.getByTestId('orders-table')).toBeInTheDocument());
    const chip = row(14).getByText('Pending').closest('.MuiChip-root') as HTMLElement;
    expect(chip.style.backgroundColor || getComputedStyle(chip).backgroundColor).toContain('--qc-tint-blue');
    expect(getComputedStyle(chip).color).toContain('--qc-ink-blue');
  });

  it(`shows the latest ${RECENT_ORDERS} until asked for all of them`, async () => {
    const many = Array.from({ length: RECENT_ORDERS + 2 }, (_, i) => order({ order_id: 200 + i }));
    mount(ordersResponse(many));
    await waitFor(() => expect(screen.getByTestId('orders-table')).toBeInTheDocument());
    expect(screen.getAllByTestId(/^order-\d+$/)).toHaveLength(RECENT_ORDERS);

    fireEvent.click(screen.getByRole('button', { name: `Show all ${RECENT_ORDERS + 2} orders` }));
    expect(screen.getAllByTestId(/^order-\d+$/)).toHaveLength(RECENT_ORDERS + 2);
    fireEvent.click(screen.getByRole('button', { name: 'Show the latest orders' }));
    expect(screen.getAllByTestId(/^order-\d+$/)).toHaveLength(RECENT_ORDERS);
  });

  describe('cancelling', () => {
    it('offers no Cancel unless told it may', async () => {
      mount(ordersResponse([PENDING], { pending_count: 1 }));
      await waitFor(() => expect(screen.getByTestId('orders-table')).toBeInTheDocument());
      expect(screen.queryByRole('button', { name: /Cancel/ })).toBeNull();
    });

    it('cancels a pending order and reads the orders again', async () => {
      const api = mount(ordersResponse([PENDING, FILLED], { pending_count: 1 }), { canCancel: true });
      await waitFor(() => expect(screen.getByTestId('orders-table')).toBeInTheDocument());
      // Only the pending order has the button.
      expect(row(12).queryByRole('button')).toBeNull();

      fireEvent.click(row(14).getByRole('button', { name: 'Cancel order to sell 1.5 intc' }));

      await waitFor(() => expect(api.calls).toHaveLength(3));
      expect(api.calls[1]).toEqual(['/api/portfolio/orders/14/cancel?book_id=7', expect.objectContaining({ method: 'POST' })]);
      expect(api.calls[2][0]).toBe('/api/portfolio/orders?book_id=7');
    });

    it('shows why a cancel was refused', async () => {
      mockApi([
        [/\/orders\/\d+\/cancel/, { __status: 422, error: 'only a pending order can be cancelled: this one is filled' }],
        ['/api/portfolio/orders', ordersResponse([PENDING], { pending_count: 1 })],
      ]);
      renderWithProviders(<OrdersList bookId={7} canCancel />);
      fireEvent.click(await screen.findByRole('button', { name: /Cancel order/ }));
      expect(await row(14).findByText(/this one is filled/)).toBeInTheDocument();
    });
  });
});
