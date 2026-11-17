import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import PaperOrderDialog from './PaperOrderDialog';
import {
  cashLedger, holdingStops, mockApi, order, renderWithProviders, stopLossResponse, stopRow,
} from '../../testUtils';

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

  describe('the stop a buy asks for (#431 step 4.3)', () => {
    const CARD: [string, object] = ['/stop-loss', stopLossResponse({ symbol: 'INTC' })];
    const cardCalls = (api: { calls: Array<[string, RequestInit | undefined]> }) =>
      api.calls.filter(([url]) => url.includes('/stop-loss'));
    const openStop = () => fireEvent.click(screen.getByRole('checkbox', { name: 'Add a stop' }));
    const buy = () => fireEvent.click(screen.getByRole('button', { name: 'Buy' }));

    it('sends no stop, and never asks for the stop card, while the field is closed', async () => {
      const { api } = mount({}, order(), [CARD]);
      expect(screen.queryByTestId('order-stop')).toBeNull();
      type(/^Shares/, '2');
      buy();
      await screen.findByTestId('order-result');
      expect(posts(api)[0][1].stop).toBeUndefined();
      expect(cardCalls(api)).toHaveLength(0);
    });

    it('loads the stop card once the field is opened, and only then', async () => {
      const { api } = mount({}, order(), [CARD]);
      openStop();
      expect(screen.getByText(/Loading the stop card's suggestions/)).toBeInTheDocument();
      expect(await screen.findByRole('button', { name: 'Technical $173.54' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Trailing 6.5%' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Confluence $168.65' })).toBeInTheDocument();
      expect(cardCalls(api)).toHaveLength(1);
      expect(cardCalls(api)[0][0]).toContain('/api/securities/INTC/stop-loss?confluence=stop');
    });

    it('sends a typed fixed level as a manual stop and says it was set', async () => {
      const { api } = mount(
        {},
        order({ stop_kind: 'fixed', stop_level: 45, stop_method: 'manual', stop_set: true }),
        [CARD],
      );
      type(/^Shares/, '2');
      openStop();
      type(/Stop level/, '45');
      buy();

      expect(await screen.findByTestId('order-result')).toHaveTextContent(
        'Bought INTC: 2 @ $50.00. Its stop is set at $45.00.',
      );
      expect(posts(api)[0][1].stop).toEqual({ kind: 'fixed', stop_level: 45, method: 'manual' });
    });

    it('fills the field from a suggestion and records what was suggested, even after an edit', async () => {
      const { api } = mount({}, order(), [CARD]);
      type(/^Shares/, '2');
      openStop();
      fireEvent.click(await screen.findByRole('button', { name: 'Technical $173.54' }));
      expect((screen.getByLabelText(/Stop level/) as HTMLInputElement).value).toBe('173.54');
      type(/Stop level/, '172');
      buy();

      await screen.findByTestId('order-result');
      expect(posts(api)[0][1].stop).toEqual({
        kind: 'fixed', stop_level: 172, method: 'technical', proposed_level: 173.54,
      });
    });

    it('sends the trailing suggestion as a percentage, with the level the card worked out', async () => {
      const { api } = mount(
        {},
        order({ stop_kind: 'trailing', stop_trail_pct: 6.5, stop_method: 'trailing', stop_set: true }),
        [CARD],
      );
      type(/^Shares/, '2');
      openStop();
      fireEvent.click(await screen.findByRole('button', { name: 'Trailing 6.5%' }));
      expect((screen.getByLabelText(/Trail percent/) as HTMLInputElement).value).toBe('6.5');
      buy();

      expect(await screen.findByTestId('order-result')).toHaveTextContent(
        'Its stop is set, trailing 6.5% below the fill price.',
      );
      expect(posts(api)[0][1].stop).toEqual({
        kind: 'trailing', trail_pct: 6.5, method: 'trailing', proposed_level: 168.3,
      });
    });

    it('forgets a suggestion when the kind is changed by hand', async () => {
      const { api } = mount({}, order(), [CARD]);
      type(/^Shares/, '2');
      openStop();
      fireEvent.click(await screen.findByRole('button', { name: 'Confluence $168.65' }));
      fireEvent.click(screen.getByRole('button', { name: 'Trailing' }));
      expect((screen.getByLabelText(/Trail percent/) as HTMLInputElement).value).toBe('');
      type(/Trail percent/, '8');
      buy();

      await screen.findByTestId('order-result');
      expect(posts(api)[0][1].stop).toEqual({ kind: 'trailing', trail_pct: 8, method: 'manual' });
    });

    it('leaves out the confluence suggestion when no zone qualified', async () => {
      const response = stopLossResponse();
      mount({}, order(), [[
        '/stop-loss',
        { ...response, stops: { ...response.stops, confluence_stop: null, confluence_stop_reason: 'no zone' } },
      ]]);
      openStop();
      await screen.findByRole('button', { name: 'Technical $173.54' });
      expect(screen.queryByRole('button', { name: /Confluence/ })).toBeNull();
    });

    it('still takes a typed level when the stop card fails to load', async () => {
      const { api } = mount({}, order(), [['/stop-loss', { __status: 500, error: 'boom' }]]);
      type(/^Shares/, '2');
      openStop();
      expect(await screen.findByText(/suggestions could not be loaded/)).toBeInTheDocument();
      type(/Stop level/, '45');
      buy();
      await screen.findByTestId('order-result');
      expect(posts(api)[0][1].stop).toMatchObject({ kind: 'fixed', stop_level: 45 });
    });

    it('asks for a figure before it calls the server', async () => {
      const { api } = mount({}, order(), [CARD]);
      type(/^Shares/, '2');
      openStop();
      buy();
      expect(await screen.findByRole('alert')).toHaveTextContent('The stop level must be more than zero.');

      fireEvent.click(screen.getByRole('button', { name: 'Trailing' }));
      type(/Trail percent/, '100');
      buy();
      expect(screen.getByRole('alert')).toHaveTextContent('The trail must be less than 100 percent.');
      type(/Trail percent/, '0');
      buy();
      expect(screen.getByRole('alert')).toHaveTextContent('The trail must be more than zero percent.');
      expect(posts(api)).toHaveLength(0);
    });

    it('says a queued order gets its stop when it fills', async () => {
      mount(
        {},
        order({
          status: 'pending', session_date: '2026-06-16', resolved_at: null, filled_shares: null,
          fill_price: null, price_source: null, quote_time: null,
          stop_kind: 'fixed', stop_level: 45, stop_method: 'manual',
        }),
        [CARD],
      );
      type(/^Shares/, '2');
      openStop();
      type(/Stop level/, '45');
      buy();
      expect(await screen.findByTestId('order-result')).toHaveTextContent(
        /fills at its opening price.*Its stop is set when the order fills\./,
      );
    });

    it('says a buy was rejected because it opened at or below its stop', async () => {
      mount(
        {},
        order({
          ...REJECTED, reject_reason: 'stop_not_below_fill',
          stop_kind: 'fixed', stop_level: 55, stop_method: 'manual',
        }),
        [CARD],
      );
      type(/^Shares/, '2');
      openStop();
      type(/Stop level/, '55');
      buy();
      const result = await screen.findByTestId('order-result');
      expect(result).toHaveTextContent(
        'Rejected: it opened at or below the stop it asked for, so no position was opened. Nothing changed.',
      );
      expect(result).not.toHaveTextContent('Its stop is set');
    });

    describe('a symbol that already has a stop in the book (decision 65)', () => {
      const held = (stops: object[]): [string, object] => ['/api/portfolio/stops/holdings', holdingStops(stops)];

      it('warns that the new stop replaces it, and asks about the chosen book', async () => {
        const { api } = mount({}, order(), [CARD, held([stopRow({ symbol: 'INTC', stop_level: 45 })])]);
        openStop();
        const warning = await screen.findByTestId('order-stop-replaces');
        expect(warning).toHaveTextContent('INTC already has a stop in this book, at $45.00.');
        expect(warning).toHaveTextContent('replaces it when the buy fills');
        const asked = api.calls.map(([url]) => url).filter((url) => url.includes('/stops/holdings'));
        expect(asked).toEqual(['/api/portfolio/stops/holdings?book_id=7']);
      });

      it('names a trailing stop by its percentage', async () => {
        mount({}, order(), [
          CARD,
          held([stopRow({ symbol: 'INTC', kind: 'trailing', stop_level: 46, trail_pct: 8, high_water_mark: 50 })]),
        ]);
        openStop();
        expect(await screen.findByTestId('order-stop-replaces')).toHaveTextContent('at $46.00, trailing 8%');
      });

      it('says nothing for another symbol\'s stop, a reached stop, or no stop', async () => {
        mount({}, order(), [
          CARD,
          held([
            stopRow({ symbol: 'AAPL' }),
            stopRow({ symbol: 'INTC', status: 'triggered', triggered_session: '2026-06-16', trigger_price: 44 }),
          ]),
        ]);
        openStop();
        await screen.findByRole('button', { name: 'Technical $173.54' });
        expect(screen.queryByTestId('order-stop-replaces')).toBeNull();
      });

      it('does not ask about the book\'s stops until the field is opened', async () => {
        const { api } = mount({}, order(), [CARD, held([stopRow()])]);
        await screen.findByText(/Cash in this book/);
        expect(api.calls.some(([url]) => url.includes('/stops/holdings'))).toBe(false);
      });
    });

    it('shows the server\'s refusal of a stop that is not below the fill price', async () => {
      mount(
        {},
        { __status: 422, error: 'the stop must be below the price the order fills at, which is 50.00' },
        [CARD],
      );
      type(/^Shares/, '2');
      openStop();
      type(/Stop level/, '60');
      buy();
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'the stop must be below the price the order fills at, which is 50.00',
      );
    });

    it('offers no stop on a sell', () => {
      mount({ side: 'sell', books: [BOOKS[0]], heldShares: 3 }, order({ side: 'sell' }));
      expect(screen.queryByRole('checkbox', { name: 'Add a stop' })).toBeNull();
    });
  });
});
