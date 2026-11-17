import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import StopPanel from './StopPanel';
import { holdingStops, mockApi, renderWithProviders, stopLossResponse, stopRow } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const book = (book_id: number, name: string) => ({
  book_id, name, kind: 'paper' as const, starting_cash: 0, created_at: '2026-11-01T00:00:00Z', archived_at: null,
});

type Responder = object | ((url: string, init?: RequestInit) => object | Promise<object>);

function mount(
  props: Partial<{ heldInReal: boolean; paperBooks: ReturnType<typeof book>[] }> = {},
  { held = holdingStops([]), set = stopRow({ symbol: 'XYZ', stop_level: 170 }) as Responder,
    card = stopLossResponse() as Responder } = {},
) {
  const api = mockApi([
    ['/api/portfolio/stops/holdings', held],
    [/\/api\/portfolio\/stops$/, set],
    ['/stop-loss', card],
  ] as never);
  renderWithProviders(
    <StopPanel symbol="XYZ" heldInReal paperBooks={[]} {...props} />,
  );
  return api;
}

const posts = (api: { calls: Array<[string, RequestInit | undefined]> }) =>
  api.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([url, init]) => [url, JSON.parse(String(init?.body))] as const);

const type = (label: RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const open = () => fireEvent.click(screen.getByRole('button', { name: /Set a stop|Replace this stop/ }));

describe('StopPanel', () => {
  it('shows nothing to an owner with no holding and no paper book', () => {
    mount({ heldInReal: false });
    expect(screen.queryByTestId('stop-panel')).toBeNull();
  });

  it('says the holding has no stop, and asks for the card only once the form is open', async () => {
    const api = mount();
    expect(await screen.findByText('No stop in this book.')).toBeInTheDocument();
    expect(api.calls.some(([url]) => url.includes('/stop-loss'))).toBe(false);
    open();
    expect(await screen.findByTestId('stop-panel-suggestions')).toBeInTheDocument();
    expect(api.calls.some(([url]) => url.includes('/stop-loss?confluence=full'))).toBe(true);
  });

  it("shows each suggestion with the card's reasoning", async () => {
    mount();
    open();
    const list = await screen.findByTestId('stop-panel-suggestions');
    expect(within(list).getByText(/-3\.6% from the price, under the sma 20 at \$176\.00\./)).toBeInTheDocument();
    expect(within(list).getByText(/Trails 6\.5% below the high, \$168\.30 if placed today/)).toBeInTheDocument();
    expect(within(list).getByText(/under the support zone \$170\.00–\$171\.50, where 3 methods agree/)).toBeInTheDocument();
  });

  it('sets a suggested stop, keeping what the card proposed beside an override', async () => {
    const api = mount();
    open();
    fireEvent.click(await screen.findByRole('button', { name: 'Confluence $168.65' }));
    expect(screen.getByLabelText(/Stop level/)).toHaveValue(168.65);
    type(/Stop level/, '167');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));

    expect(await screen.findByText('Stop set at $170.00.')).toBeInTheDocument();
    const [[url, body]] = posts(api);
    expect(url).toBe('/api/portfolio/stops');
    expect(body).toEqual({
      symbol: 'XYZ', kind: 'fixed', stop_level: 167, method: 'confluence', proposed_level: 168.65,
    });
    // The form closes; the stops are read again.
    expect(screen.queryByTestId('stop-panel-form')).toBeNull();
  });

  it('sets a trailing stop from the card as a percentage', async () => {
    const api = mount();
    open();
    fireEvent.click(await screen.findByRole('button', { name: 'Trailing 6.5%' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(posts(api)[0][1]).toEqual({
      symbol: 'XYZ', kind: 'trailing', trail_pct: 6.5, method: 'trailing', proposed_level: 168.3,
    });
  });

  it('mirrors a broker order with its details, as a manual stop at a broker', async () => {
    const api = mount();
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Mirror a broker order' }));
    expect(screen.queryByTestId('stop-panel-suggestions')).toBeNull();
    type(/Stop level/, '165.5');
    type(/Shares/, '10');
    type(/Brokerage/, '  Schwab ');
    fireEvent.mouseDown(screen.getByLabelText(/Order type/));
    fireEvent.click(await screen.findByRole('option', { name: 'stop-limit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));

    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(posts(api)[0][1]).toEqual({
      symbol: 'XYZ', kind: 'fixed', stop_level: 165.5, method: 'manual', at_broker: true,
      broker_quantity: 10, broker_order_type: 'stop_limit', brokerage: 'Schwab',
    });
  });

  it('offers only a trailing stop order for a trailing mirror, and sends no detail not given', async () => {
    const api = mount();
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Mirror a broker order' }));
    fireEvent.click(screen.getByRole('button', { name: 'Trailing' }));
    fireEvent.mouseDown(screen.getByLabelText(/Order type/));
    const options = (await screen.findAllByRole('option')).map((o) => o.textContent);
    expect(options).toEqual(['Not given', 'trailing stop']);
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    type(/Trail percent/, '8');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));

    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(posts(api)[0][1]).toEqual({
      symbol: 'XYZ', kind: 'trailing', trail_pct: 8, method: 'manual', at_broker: true,
    });
  });

  it('checks the figures before it calls the server', async () => {
    const api = mount();
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Mirror a broker order' }));
    type(/Stop level/, '-1');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
    expect(await screen.findByText('The stop level must be more than zero.')).toBeInTheDocument();
    type(/Stop level/, '160');
    type(/Shares/, '0');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
    expect(await screen.findByText("The broker order's share count must be more than zero.")).toBeInTheDocument();
    expect(posts(api)).toEqual([]);
  });

  it("shows the server's refusal and keeps the form open", async () => {
    mount({}, { set: { __status: 422, error: 'the stop must be below the last close (180.00)' } });
    open();
    type(/Stop level/, '190');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
    expect(await screen.findByText(/below the last close/)).toBeInTheDocument();
    expect(screen.getByTestId('stop-panel-form')).toBeInTheDocument();
  });

  it('shows the current stop, where it lives, and warns that a new one replaces it', async () => {
    mount({}, {
      held: holdingStops([stopRow({
        symbol: 'XYZ', stop_level: 170, at_broker: true, brokerage: 'Schwab', broker_quantity: 10,
        broker_order_type: 'stop',
      })]),
    });
    await screen.findByText('$170.00');
    const current = screen.getByTestId('stop-panel-current');
    expect(current).toHaveTextContent('At a broker');
    expect(current).toHaveTextContent('Placed at Schwab as a stop order for 10 shares.');
    open();
    expect(screen.getByTestId('stop-panel-replaces')).toHaveTextContent(
      'replaces the one at $170.00. Change or cancel that order at your broker too',
    );
  });

  it('a stop only here says so, and its replacement warning names no broker', async () => {
    mount({}, { held: holdingStops([stopRow({ symbol: 'XYZ', stop_level: 170 })]) });
    expect(await screen.findByText('Only here')).toBeInTheDocument();
    open();
    expect(screen.getByTestId('stop-panel-replaces')).toHaveTextContent('replaces the one at $170.00.');
    expect(screen.getByTestId('stop-panel-replaces')).not.toHaveTextContent('broker');
  });

  it('a reached stop is shown as reached, and a new one is set, not replaced', async () => {
    mount({}, {
      held: holdingStops([stopRow({
        symbol: 'XYZ', status: 'triggered', trigger_price: 168, triggered_session: '2026-06-16',
      })]),
    });
    expect(await screen.findByText('Reached at $168.00')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set a stop' })).toBeInTheDocument();
  });

  it("names a paper book's stop by its book, and offers it no broker", async () => {
    const api = mount({ heldInReal: false, paperBooks: [book(7, 'Momentum')] });
    await screen.findByText('No stop in this book.');
    expect(api.calls[0][0]).toBe('/api/portfolio/stops/holdings?book_id=7');
    open();
    expect(screen.getByRole('button', { name: 'Mirror a broker order' })).toBeDisabled();
    expect(screen.getByText('A paper book has no broker, so its stops are set here only.')).toBeInTheDocument();
    type(/Stop level/, '160');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(posts(api)[0][1]).toEqual({ symbol: 'XYZ', kind: 'fixed', stop_level: 160, method: 'manual', book_id: 7 });
  });

  it('switches book with the chips, and leaves the broker path for a paper book', async () => {
    const api = mount({ paperBooks: [book(7, 'Momentum')] });
    await screen.findByText('No stop in this book.');
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Mirror a broker order' }));
    expect(screen.getByTestId('stop-panel-broker')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Momentum' }));
    await waitFor(() =>
      expect(api.calls.some(([url]) => url === '/api/portfolio/stops/holdings?book_id=7')).toBe(true));
    expect(screen.queryByTestId('stop-panel-broker')).toBeNull();
  });

  it('drops a broker draft when the book is switched and back (#570 review)', async () => {
    mount({ paperBooks: [book(7, 'Momentum')] });
    await screen.findByText('No stop in this book.');
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Mirror a broker order' }));
    type(/Shares/, '10');
    type(/Brokerage/, 'Schwab');
    fireEvent.click(screen.getByRole('button', { name: 'Momentum' }));
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    fireEvent.click(screen.getByRole('button', { name: 'Mirror a broker order' }));
    expect(screen.getByLabelText(/Shares/)).toHaveValue(null);
    expect(screen.getByLabelText(/Brokerage/)).toHaveValue('');
  });

  it('still takes a typed figure when the card cannot be loaded', async () => {
    const api = mount({}, { card: { __status: 500, error: 'boom' } });
    open();
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
    type(/Stop level/, '160');
    fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
  });
});
