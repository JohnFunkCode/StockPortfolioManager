import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import PaperOrderDialog from './PaperOrderDialog';
import { cashLedger, mockApi, order, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const book = (book_id: number, name: string) => ({
  book_id, name, kind: 'paper' as const, starting_cash: 0, created_at: '2026-11-01T00:00:00Z', archived_at: null,
});
const BOOKS = [book(7, 'Momentum'), book(8, 'Value')];
const REJECTED = { status: 'rejected', filled_shares: null, fill_price: null, price_source: null, quote_time: null };
const PAPER_CASH = cashLedger(undefined, { book_kind: 'paper', balance: 2500 });

type Responder = object | ((url: string, init?: RequestInit) => object | Promise<object>);

function mount(props: object, placed: Responder, extra: Array<[string | RegExp, Responder]> = []) {
  const onClose = vi.fn();
  const api = mockApi([
    ...extra,
    [/\/api\/portfolio\/orders$/, placed],
    ['/api/portfolio/cash', PAPER_CASH],
  ] as never);
  renderWithProviders(
    <PaperOrderDialog symbol="INTC" side="buy" books={BOOKS} onClose={onClose} {...props} />,
  );
  return { api, onClose };
}

const posts = (api: { calls: Array<[string, RequestInit | undefined]> }) =>
  api.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([url, init]) => [url, JSON.parse(String(init?.body))] as const);

const type = (label: RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe('PaperOrderDialog', () => {
  it('buys a number of shares in the first paper book and says what filled', async () => {
    const { api, onClose } = mount({}, order());
    expect(screen.getByRole('heading', { name: 'Buy INTC on paper' })).toBeInTheDocument();
    expect(await screen.findByText(/Cash in this book: \$2,500\.00/)).toBeInTheDocument();

    type(/^Shares/, '2');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));

    expect(await screen.findByTestId('order-result')).toHaveTextContent('Bought INTC: 2 @ $50.00.');
    const [[url, body]] = posts(api);
    expect(url).toBe('/api/portfolio/orders');
    expect(body).toMatchObject({ book_id: 7, symbol: 'INTC', side: 'buy', shares: 2 });
    expect(body.amount).toBeUndefined();
    expect(body.idempotency_key).toEqual(expect.any(String));
    // Nothing is left to send: the form goes and the button says Done.
    expect(screen.queryByRole('button', { name: 'Buy' })).toBeNull();
    expect(screen.queryByLabelText(/^Shares/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('buys a dollar amount, and never sends a price or a date', async () => {
    const { api } = mount({}, order({ requested_shares: null, requested_amount: 500, filled_shares: 10 }));
    fireEvent.click(screen.getByRole('button', { name: 'Dollars' }));
    type(/Dollar amount/, '500');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));

    await screen.findByTestId('order-result');
    const [[, body]] = posts(api);
    expect(Object.keys(body).sort()).toEqual(['amount', 'book_id', 'idempotency_key', 'side', 'symbol']);
    expect(body.amount).toBe(500);
  });

  it('asks for a size before it calls the server', async () => {
    const { api } = mount({}, order());
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Shares must be more than zero.');
    type(/^Shares/, '-1');
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it('says a queued order waits for its session', async () => {
    mount({}, order({ status: 'pending', session_date: '2026-06-16', filled_shares: null, fill_price: null }));
    type(/^Shares/, '2');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    const result = await screen.findByTestId('order-result');
    expect(result).toHaveTextContent(/Queued\. The market is closed/);
    expect(result).toHaveTextContent('Jun 16, 2026');
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  it('gives the reason for a rejection, and a second try is a new order', async () => {
    const { api } = mount({}, order({ ...REJECTED, reject_reason: 'insufficient_cash' }));
    type(/^Shares/, '2');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    expect(await screen.findByTestId('order-result')).toHaveTextContent(
      'Rejected: not enough cash. Nothing changed.',
    );
    // The form stays, and a buy has no manual close.
    expect(screen.queryByRole('button', { name: 'Close position' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    await waitFor(() => expect(posts(api)).toHaveLength(2));
    const [[, first], [, second]] = posts(api);
    expect(second.idempotency_key).not.toBe(first.idempotency_key);
  });

  it('repeats the key when the server never answered, and shows the error', async () => {
    const { api } = mount({}, { __status: 500, error: 'boom' });
    type(/^Shares/, '2');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    await waitFor(() => expect(posts(api)).toHaveLength(2));
    const [[, first], [, second]] = posts(api);
    expect(second.idempotency_key).toBe(first.idempotency_key);
  });

  it('sends the order to the book that was picked', async () => {
    const { api } = mount({}, order({ book_id: 8 }));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Paper book' }));
    fireEvent.click(within(screen.getByRole('listbox')).getByText('Value'));
    type(/^Shares/, '1');
    fireEvent.click(screen.getByRole('button', { name: 'Buy' }));
    await screen.findByTestId('order-result');
    expect(posts(api)[0][1].book_id).toBe(8);
  });

  it('sells the shares the book holds, with no book picker and no dollars', async () => {
    const { api } = mount(
      { side: 'sell', books: [BOOKS[0]], heldShares: 10 },
      order({ side: 'sell', requested_shares: 10, filled_shares: 10 }),
    );
    expect(screen.getByRole('heading', { name: 'Sell INTC on paper' })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Shares/)).toHaveValue(10);
    expect(screen.getByText(/This book holds 10 INTC/)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Dollars' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Close position' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    expect(await screen.findByTestId('order-result')).toHaveTextContent('Sold INTC: 10 @ $50.00.');
    expect(posts(api)[0][1]).toMatchObject({ book_id: 7, side: 'sell', shares: 10 });
  });

  it('offers to close a position whose sell found no quote, at the last price on record', async () => {
    const closed = order({
      order_id: 20, side: 'sell', kind: 'manual_close', requested_shares: 10, filled_shares: 10,
      fill_price: 3.2, price_source: null, quote_time: '2026-06-10T00:00:00Z',
    });
    const { api } = mount(
      { side: 'sell', books: [BOOKS[0]], heldShares: 10 },
      order({ ...REJECTED, side: 'sell', reject_reason: 'no_quote' }),
      [['/api/portfolio/orders/manual-close', closed]],
    );
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    expect(await screen.findByTestId('order-result')).toHaveTextContent('no quote for this symbol');

    fireEvent.click(await screen.findByRole('button', { name: 'Close position' }));
    await waitFor(() =>
      expect(screen.getByTestId('order-result')).toHaveTextContent(
        'Closed INTC: 10 @ $3.20, the last price on record, from Jun 10, 2026.',
      ),
    );
    const close = posts(api).find(([url]) => url.endsWith('/manual-close'));
    // The owner sends no price and no share count.
    expect(Object.keys(close![1]).sort()).toEqual(['book_id', 'idempotency_key', 'symbol']);
    expect(screen.queryByRole('button', { name: 'Close position' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  it('offers the manual close at once for a position with no price, and shows a refusal', async () => {
    mount(
      { side: 'sell', books: [BOOKS[0]], heldShares: 4, unpriced: true },
      order(),
      [['/api/portfolio/orders/manual-close', { __status: 422, error: 'INTC has a quote: sell it with an order' }]],
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close position' }));
    expect(await screen.findByText('INTC has a quote: sell it with an order')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sell' })).toBeInTheDocument();
  });
});
