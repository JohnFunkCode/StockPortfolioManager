import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import RealizedPnl from './RealizedPnl';
import {
  mockApi,
  realizedResponse,
  realizedSale,
  realizedSymbol,
  renderWithProviders,
} from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const REALIZED = '/api/portfolio/realized';

function mount(payload: object = realizedResponse(), props: { bookId?: number } = {}) {
  const api = mockApi([[REALIZED, payload]]);
  renderWithProviders(<RealizedPnl {...props} />);
  return api;
}

const table = () => within(screen.getByTestId('realized-table'));
const symbolOrder = () =>
  Array.from(document.querySelectorAll('[data-testid^="realized-row-"]')).map((row) =>
    row.getAttribute('data-testid')!.replace('realized-row-', ''),
  );

describe('RealizedPnl', () => {
  it('says it is loading while the request is in flight', () => {
    mockApi([[REALIZED, () => new Promise<object>(() => {})]]);
    renderWithProviders(<RealizedPnl />);
    expect(screen.getByText(/Loading realized P\/L/)).toBeInTheDocument();
    expect(screen.queryByTestId('realized-table')).not.toBeInTheDocument();
  });

  it('shows an error, and no table, when the request fails', async () => {
    mockApi([[REALIZED, () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<RealizedPnl />);
    await waitFor(() => expect(screen.getByText(/Couldn't load realized P\/L/)).toBeInTheDocument());
    expect(screen.queryByTestId('realized-table')).not.toBeInTheDocument();
  });

  it('opens on Year, for the real book, and shows the window the server resolved', async () => {
    const api = mount();
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0][0]).toContain('/api/portfolio/realized?period=year');
    expect(api.calls[0][0]).not.toContain('book_id');
    expect(screen.getByText('Sold Nov 14, 2025 to Nov 14, 2026')).toBeInTheDocument();
  });

  it("shows the server's totals and each symbol's figures", async () => {
    mount(
      realizedResponse(
        [
          realizedSymbol(),
          realizedSymbol({
            symbol: 'WDC',
            cost_basis: 400,
            proceeds: 360,
            realized_pnl: -40,
            bar_base: 400,
            bar_gain: 0,
            bar_loss: -40,
          }),
        ],
        {
          totals: {
            symbol_count: 2,
            sale_count: 3,
            fx_missing_count: 0,
            basis_missing_count: 0,
            cost_basis: 550,
            proceeds: 560,
            realized_pnl: 10,
            realized_pnl_pct: 1.82,
          },
        },
      ),
    );
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());

    // The summary is the payload's totals, not a sum made on the page.
    expect(screen.getByText('$10.00')).toBeInTheDocument();
    expect(screen.getByText('1.82%')).toBeInTheDocument();
    expect(screen.getByText('$550.00')).toBeInTheDocument();
    expect(screen.getByText('$560.00')).toBeInTheDocument();
    expect(screen.getByText('3 in 2 symbols')).toBeInTheDocument();

    const wdc = within(screen.getByTestId('realized-row-WDC'));
    expect(wdc.getByText('$400.00')).toBeInTheDocument();
    expect(wdc.getByText('$360.00')).toBeInTheDocument();
    expect(wdc.getByText('-$40.00')).toBeInTheDocument();
    expect(screen.queryByTestId('realized-left-out')).not.toBeInTheDocument();
  });

  it('says what the figures leave out', async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
    expect(screen.getByText(/Excludes fees and tax adjustments/)).toBeInTheDocument();
  });

  it('draws the chart from the same payload, in the order the rows arrive', async () => {
    const api = mount(
      realizedResponse([
        realizedSymbol({ symbol: 'SMALL', bar_base: 10, bar_gain: 9, bar_loss: 0 }),
        realizedSymbol({ symbol: 'BIG', bar_base: 900, bar_gain: 1, bar_loss: 0 }),
      ]),
    );
    await waitFor(() => expect(screen.getByTestId('realized-pnl-chart')).toBeInTheDocument());
    expect(api.calls).toHaveLength(1);
    // Best realized first, as sent: the allocation chart would put BIG first.
    const bars = Array.from(document.querySelectorAll('[data-testid^="realized-base-"]'));
    expect(bars.map((b) => b.getAttribute('data-testid'))).toEqual([
      'realized-base-SMALL',
      'realized-base-BIG',
    ]);
    expect(Number(bars[0].getAttribute('x'))).toBeLessThan(Number(bars[1].getAttribute('x')));
  });

  it('marks a symbol the book still holds, and only that one', async () => {
    mount(
      realizedResponse([
        realizedSymbol({ still_held: true }),
        realizedSymbol({ symbol: 'WDC', still_held: false }),
      ]),
    );
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
    expect(within(screen.getByTestId('realized-row-INTC')).getByText('still held')).toBeInTheDocument();
    expect(within(screen.getByTestId('realized-row-WDC')).queryByText('still held')).toBeNull();
  });

  it('expands a symbol to its sales without another request', async () => {
    const api = mount(
      realizedResponse([
        realizedSymbol({
          shares_closed: 7.5,
          sale_count: 2,
          sales: [
            realizedSale({ notes: 'Trimmed into strength' }),
            realizedSale({
              sale_id: 2,
              shares_sold: 2.5,
              sale_price: 44,
              sale_trade_date: '2026-07-01',
              realized_pnl: 35,
              realized_pnl_pct: 46.67,
            }),
          ],
        }),
      ]),
    );
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
    expect(screen.queryByTestId('realized-sale-1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show INTC sales' }));

    expect(screen.getByText(/7\.5 shares closed in 2 sales · realized 33\.33%/)).toBeInTheDocument();
    const first = within(screen.getByTestId('realized-sale-1'));
    expect(first.getByText('Jan 2, 2026')).toBeInTheDocument();
    expect(first.getByText('Jun 15, 2026')).toBeInTheDocument();
    expect(first.getByText('$30.00')).toBeInTheDocument();
    expect(first.getByText('$40.00')).toBeInTheDocument();
    expect(first.getByText('$50.00')).toBeInTheDocument();
    expect(first.getByText('Trimmed into strength')).toBeInTheDocument();
    const second = within(screen.getByTestId('realized-sale-2'));
    expect(second.getByText('2.5')).toBeInTheDocument();
    expect(second.getByText('46.67%')).toBeInTheDocument();
    expect(api.calls).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Hide INTC sales' }));
    await waitFor(() => expect(screen.queryByTestId('realized-sale-1')).not.toBeInTheDocument());
  });

  it('shows a converted sale in dollars, with its own currency and both rates', async () => {
    mount(
      realizedResponse([
        realizedSymbol({
          symbol: 'SAP.DE',
          cost_basis: 165,
          proceeds: 200,
          realized_pnl: 35,
          sales: [
            realizedSale({
              sale_id: 7,
              symbol: 'SAP.DE',
              currency: 'EUR',
              fx_rate_at_purchase: 1.1,
              fx_rate_at_sale: 1.0,
              cost_basis: 165,
              proceeds: 200,
              realized_pnl: 35,
              realized_pnl_native: 50,
            }),
          ],
        }),
      ]),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Show SAP.DE sales' }));
    const sale = await screen.findByTestId('realized-sale-7');
    expect(within(sale).getByText('$35.00')).toBeInTheDocument();
    expect(within(sale).queryByText('no FX rate')).not.toBeInTheDocument();
    expect(screen.getByTestId('realized-sale-fx-7')).toHaveTextContent('€50.00 at 1.1000 → 1.0000');
  });

  it('shows no conversion line under a dollar sale', async () => {
    mount(realizedResponse([realizedSymbol()]));

    fireEvent.click(await screen.findByRole('button', { name: 'Show INTC sales' }));
    await screen.findByTestId('realized-sale-1');
    expect(screen.queryByTestId('realized-sale-fx-1')).not.toBeInTheDocument();
  });

  it('flags a sale with no FX rate, shows its own currency, and counts it', async () => {
    mount(
      realizedResponse(
        [
          realizedSymbol({
            symbol: 'SAP.DE',
            fx_missing_count: 1,
            cost_basis: null,
            proceeds: null,
            realized_pnl: null,
            realized_pnl_pct: null,
            bar_base: null,
            bar_gain: null,
            bar_loss: null,
            sales: [
              realizedSale({
                symbol: 'SAP.DE',
                currency: 'EUR',
                fx_missing: true,
                cost_basis: null,
                proceeds: null,
                realized_pnl: null,
                realized_pnl_pct: null,
              }),
            ],
          }),
        ],
        {
          totals: {
            symbol_count: 1,
            sale_count: 1,
            fx_missing_count: 1,
            basis_missing_count: 0,
            cost_basis: null,
            proceeds: null,
            realized_pnl: null,
            realized_pnl_pct: null,
          },
        },
      ),
    );
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());

    expect(screen.getByTestId('realized-left-out')).toHaveTextContent(
      'Left out of the totals: 1 sale with no FX rate.',
    );
    // Nothing has a dollar figure, so there is no chart and no zero in its place.
    expect(screen.queryByTestId('realized-pnl-chart')).not.toBeInTheDocument();
    expect(within(screen.getByTestId('realized-row-SAP.DE')).getAllByText('N/A')).toHaveLength(3);
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show SAP.DE sales' }));
    const sale = within(screen.getByTestId('realized-sale-1'));
    expect(sale.getByText('no FX rate')).toBeInTheDocument();
    expect(sale.getByText('€30.00')).toBeInTheDocument();
    expect(sale.getByText('€50.00')).toBeInTheDocument();
    expect(sale.getByText('—')).toBeInTheDocument();
  });

  it('flags a sale whose lot has no purchase price, and counts both kinds', async () => {
    mount(
      realizedResponse(
        [
          realizedSymbol({
            basis_missing_count: 1,
            sales: [
              realizedSale({
                basis_missing: true,
                purchase_price: null,
                cost_basis_native: null,
                realized_pnl_native: null,
                cost_basis: null,
                proceeds: null,
                realized_pnl: null,
                realized_pnl_pct: null,
              }),
            ],
          }),
        ],
        {
          totals: {
            symbol_count: 1,
            sale_count: 3,
            fx_missing_count: 2,
            basis_missing_count: 1,
            cost_basis: null,
            proceeds: null,
            realized_pnl: null,
            realized_pnl_pct: null,
          },
        },
      ),
    );
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
    expect(screen.getByTestId('realized-left-out')).toHaveTextContent(
      '2 sales with no FX rate, and 1 sale with no purchase price',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Show INTC sales' }));
    expect(within(screen.getByTestId('realized-sale-1')).getByText('no purchase price')).toBeInTheDocument();
  });

  it('explains an empty period and points at a longer one', async () => {
    mount(realizedResponse([]));
    await waitFor(() => expect(screen.getByTestId('realized-empty')).toBeInTheDocument());
    expect(screen.getByText('No sales in the last 365 days')).toBeInTheDocument();
    expect(screen.getByText(/Choose a longer period/)).toBeInTheDocument();
    expect(screen.queryByTestId('realized-pnl-chart')).not.toBeInTheDocument();
    expect(screen.queryByTestId('realized-table')).not.toBeInTheDocument();
  });

  it('explains a book that has never sold anything', async () => {
    mockApi([
      [/period=all/, realizedResponse([], { period: 'all', start_date: null, end_date: null })],
      [REALIZED, realizedResponse([])],
    ]);
    renderWithProviders(<RealizedPnl />);
    await waitFor(() => expect(screen.getByTestId('realized-empty')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Realized period'), { target: { value: 'all' } });
    await waitFor(() => expect(screen.getByText('Nothing has been sold yet')).toBeInTheDocument());
    // "All" has no window to print.
    expect(screen.queryByText(/^Sold /)).not.toBeInTheDocument();
  });

  it('asks the server again when the period changes, and never filters here', async () => {
    const api = mockApi([
      [/period=month/, realizedResponse([realizedSymbol({ symbol: 'WDC' })], { period: 'month' })],
      [REALIZED, realizedResponse()],
    ]);
    renderWithProviders(<RealizedPnl />);
    await waitFor(() => expect(screen.getByTestId('realized-row-INTC')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Realized period'), { target: { value: 'month' } });

    await waitFor(() => expect(screen.getByTestId('realized-row-WDC')).toBeInTheDocument());
    expect(screen.queryByTestId('realized-row-INTC')).not.toBeInTheDocument();
    expect(api.calls.map(([url]) => url)).toEqual([
      expect.stringContaining('period=year'),
      expect.stringContaining('period=month'),
    ]);
  });

  it('offers the four periods', async () => {
    mount();
    const select = screen.getByLabelText('Realized period') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['Month', 'Quarter', 'Year', 'All']);
    expect(select.value).toBe('year');
  });

  it('names the book in the request when one is given', async () => {
    const api = mount(realizedResponse(), { bookId: 7 });
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
    expect(api.calls[0][0]).toContain('period=year&book_id=7');
  });

  describe('sorting', () => {
    const three = () =>
      realizedResponse([
        realizedSymbol({ symbol: 'MSFT', realized_pnl: 90, cost_basis: 100, proceeds: 190 }),
        realizedSymbol({ symbol: 'AAPL', realized_pnl: 20, cost_basis: 900, proceeds: 920 }),
        realizedSymbol({ symbol: 'ZM', realized_pnl: null, cost_basis: null, proceeds: null }),
      ]);

    it('keeps the order the server sent: best realized first', async () => {
      mount(three());
      await waitFor(() => expect(symbolOrder()).toEqual(['MSFT', 'AAPL', 'ZM']));
    });

    it('sorts by a clicked column and reverses on a second click', async () => {
      mount(three());
      await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());

      fireEvent.click(table().getByText('Symbol'));
      expect(symbolOrder()).toEqual(['AAPL', 'MSFT', 'ZM']);
      fireEvent.click(table().getByText('Symbol'));
      expect(symbolOrder()).toEqual(['ZM', 'MSFT', 'AAPL']);

      // A money column opens largest first.
      fireEvent.click(table().getByText('Cost Basis'));
      expect(symbolOrder()).toEqual(['AAPL', 'MSFT', 'ZM']);
    });

    it('keeps a symbol with no figure last in both directions', async () => {
      mount(three());
      await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());
      fireEvent.click(table().getByText('Realized $'));
      expect(symbolOrder()).toEqual(['AAPL', 'MSFT', 'ZM']);
      fireEvent.click(table().getByText('Realized $'));
      expect(symbolOrder()).toEqual(['MSFT', 'AAPL', 'ZM']);
    });
  });
});
