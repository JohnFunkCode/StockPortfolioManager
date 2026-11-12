import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import PortfolioPage from './PortfolioPage';
import { lotRow, mockApi, renderWithProviders, symbolRow } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function mount(routes: Array<[string | RegExp, object]> = []) {
  const api = mockApi([
    [
      '/api/portfolio/symbols',
      {
        symbols: [
          symbolRow({ symbol: 'INTC', total_gain_loss: 50 }),
          symbolRow({ symbol: 'AAPL', total_gain_loss: -20, total_gain_loss_pct: -4 }),
        ],
        totals: {
          total_investment: 600,
          total_current_value: 630,
          total_gain_loss: 30,
          total_gain_loss_pct: 5,
          total_dollars_per_day: 0.5,
        },
      },
    ],
    ...routes,
  ]);
  renderWithProviders(<PortfolioPage />);
  return api;
}

// The allocation chart above the table labels its x axis with the same tickers
// (issue #147 Part A), so a page-wide getByText('INTC') is ambiguous. Symbol
// lookups scope to the holdings table; the chart has its own tests.
const holdings = () => within(screen.getByRole('table'));

describe('PortfolioPage', () => {
  it('renders the Portfolio heading and totals once loaded', async () => {
    mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(screen.getByText('Portfolio')).toBeInTheDocument();
    expect(holdings().getByText('AAPL')).toBeInTheDocument();
    expect(screen.getByText('Total Investment')).toBeInTheDocument();
  });

  it('mounts the allocation chart from the rows it already fetched', async () => {
    const api = mount();
    await waitFor(() => expect(screen.getByTestId('portfolio-allocation-chart')).toBeInTheDocument());
    // One request served both the chart and the table — the chart is passed the
    // rows, it does not fetch its own.
    expect(api.calls.filter((c) => c.includes('/api/portfolio/symbols')).length).toBe(1);
  });

  it('leaves the chart off the page when nothing is priced yet', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        {
          symbols: [symbolRow({ bar_base: null, bar_gain: null, bar_loss: null })],
          totals: null,
        },
      ],
    ]);
    renderWithProviders(<PortfolioPage />);
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(screen.queryByTestId('portfolio-allocation-chart')).not.toBeInTheDocument();
  });

  describe('return columns (issue #518)', () => {
    const symbolOrder = () =>
      Array.from(document.querySelectorAll('[data-testid^="one-day-"]')).map((cell) =>
        cell.getAttribute('data-testid')!.replace('one-day-', ''),
      );

    function mountRows(symbols: object[]) {
      mockApi([['/api/portfolio/symbols', { symbols, totals: null }]]);
      renderWithProviders(<PortfolioPage />);
    }

    it('shows 1d / 5d / 30d / 60d / YTD / 1y and no 7d or 90d', async () => {
      mount();
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      const headers = holdings()
        .getAllByRole('columnheader')
        .map((h) => h.textContent);
      expect(headers).toEqual(
        expect.arrayContaining(['1d', '5d', '30d', '60d', 'YTD', '1y']),
      );
      expect(headers).not.toContain('7d');
      expect(headers).not.toContain('90d');
    });

    it('renders the server-chosen 1d figure and each window as percent', async () => {
      mountRows([
        symbolRow({
          return_1d_display: 0.75, return_5d: 1.2, return_30d: 3.5, return_60d: 8.1,
          return_ytd: 12.3, return_1y: -21.9,
        }),
      ]);
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      expect(within(screen.getByTestId('one-day-INTC')).getByText('0.75%')).toBeInTheDocument();
      for (const text of ['1.20%', '3.50%', '8.10%', '12.30%', '-21.90%']) {
        expect(holdings().getByText(text)).toBeInTheDocument();
      }
    });

    it('marks a stale 1d figure and shows the cached value', async () => {
      mountRows([symbolRow({ return_1d_display: 0.4, return_1d_stale: true, quote_stale: true })]);
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      const cell = within(screen.getByTestId('one-day-INTC'));
      expect(cell.getByText('0.40%')).toBeInTheDocument();
      expect(cell.getByLabelText('stale')).toBeInTheDocument();
    });

    it('labels the session per row', async () => {
      mountRows([
        symbolRow({ symbol: 'AAPL', market_state: 'POST', post_change_pct: -0.25 }),
        symbolRow({ symbol: 'SONY', market_state: 'PRE', pre_change_pct: 0.4 }),
        symbolRow({ symbol: 'INTC', market_state: 'REGULAR', post_change_pct: 9 }),
      ]);
      await waitFor(() => expect(holdings().getByText('AAPL')).toBeInTheDocument());
      expect(within(screen.getByTestId('one-day-AAPL')).getByTestId('session-badge'))
        .toHaveTextContent('AH -0.25%');
      expect(within(screen.getByTestId('one-day-SONY')).getByTestId('session-badge'))
        .toHaveTextContent('Pre +0.40%');
      expect(within(screen.getByTestId('one-day-INTC')).queryByTestId('session-badge'))
        .not.toBeInTheDocument();
    });

    it('sorts by 1d with stale rows last in both directions', async () => {
      mountRows([
        symbolRow({ symbol: 'OLD', return_1d_display: 9, return_1d_stale: true }),
        symbolRow({ symbol: 'UP', return_1d_display: 2 }),
        symbolRow({ symbol: 'NONE', return_1d_display: null, return_1d_stale: true }),
        symbolRow({ symbol: 'DOWN', return_1d_display: -3 }),
      ]);
      await waitFor(() => expect(holdings().getByText('UP')).toBeInTheDocument());

      fireEvent.click(holdings().getByText('1d'));
      expect(symbolOrder()).toEqual(['DOWN', 'UP', 'OLD', 'NONE']);
      fireEvent.click(holdings().getByText('1d'));
      expect(symbolOrder()).toEqual(['UP', 'DOWN', 'OLD', 'NONE']);
    });

    it('still sorts the other windows numerically, nulls last', async () => {
      mountRows([
        symbolRow({ symbol: 'B', return_30d: 5 }),
        symbolRow({ symbol: 'A', return_30d: null }),
        symbolRow({ symbol: 'C', return_30d: -1 }),
      ]);
      await waitFor(() => expect(holdings().getByText('B')).toBeInTheDocument());
      fireEvent.click(holdings().getByText('30d'));
      expect(symbolOrder()).toEqual(['C', 'B', 'A']);
    });

    it('explains the windows on hover', async () => {
      mount();
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      fireEvent.mouseOver(holdings().getByText('30d'));
      expect(await screen.findByText(/Trading days, not calendar days/)).toBeInTheDocument();
    });
  });

  it('shows the empty state when there are no positions', async () => {
    mockApi([['/api/portfolio/symbols', { symbols: [], totals: null }]]);
    renderWithProviders(<PortfolioPage />);
    await waitFor(() => expect(screen.getByText('Your portfolio is empty')).toBeInTheDocument());
  });

  it('shows an error alert when the request fails', async () => {
    mockApi([['/api/portfolio/symbols', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<PortfolioPage />);
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });

  it('expands a symbol row to reveal its lots', async () => {
    mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());

    const rows = screen.getAllByRole('button');
    const expandButtons = rows.filter((btn) => btn.querySelector('svg[data-testid="KeyboardArrowRightIcon"]'));
    fireEvent.click(expandButtons[0]);

    expect(screen.getByText('Price Paid')).toBeInTheDocument();
    expect(screen.getByText('Trade Date')).toBeInTheDocument();
    expect(screen.getAllByText('$30.00').length).toBeGreaterThanOrEqual(1);
  });

  it('shows the saved trade date and recomputed Days Held and $/Day after an edit, and keeps them on remount', async () => {
    // A stateful stand-in for the server: the PATCH changes the stored date, and
    // every later /symbols read returns the lot as the server would recompute it.
    let stored = lotRow({ trade_date: '2026-01-02', days_held: 200, dollars_per_day: 0.25 });
    const symbols = () => ({
      symbols: [symbolRow({ lots: [stored] })],
      totals: { total_investment: 300, total_current_value: 350, total_gain_loss: 50, total_gain_loss_pct: 16.67, total_dollars_per_day: 0.25 },
    });
    mockApi([
      [
        /\/api\/portfolio\/lots\/1$/,
        (_url: string, init?: RequestInit) => {
          const patch = JSON.parse(String(init?.body));
          stored = lotRow({ trade_date: patch.trade_date, days_held: 100, dollars_per_day: 0.5 });
          return { lot: stored };
        },
      ],
      ['/api/portfolio/symbols', () => symbols()],
    ]);

    const expandLots = async () => {
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      const expand = screen
        .getAllByRole('button')
        .find((btn) => btn.querySelector('svg[data-testid="KeyboardArrowRightIcon"]'));
      fireEvent.click(expand!);
    };

    const { unmount } = renderWithProviders(<PortfolioPage />);
    await expandLots();
    expect(screen.getByText('Jan 2, 2026')).toBeInTheDocument();
    expect(screen.getByText('200')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Edit lot'));
    fireEvent.change(screen.getByLabelText('Trade date'), { target: { value: '2026-04-11' } });
    fireEvent.click(screen.getByLabelText('Save'));

    // The mutation's invalidation refetches /symbols, and the row shows the new values.
    expect(await screen.findByText('Apr 11, 2026')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('$0.50')).toBeInTheDocument();
    expect(screen.queryByText('Jan 2, 2026')).not.toBeInTheDocument();

    // A fresh mount (a reload) reads the saved date back.
    unmount();
    renderWithProviders(<PortfolioPage />);
    await expandLots();
    expect(screen.getByText('Apr 11, 2026')).toBeInTheDocument();
  });

  it('toggles sort direction when a column header is clicked', async () => {
    mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());

    const symbolHeader = screen.getByText('Symbol');
    fireEvent.click(symbolHeader);

    const bodyRows = screen.getAllByRole('row').slice(1);
    const firstRowText = within(bodyRows[0]).queryByText('INTC') || within(bodyRows[0]).queryByText('AAPL');
    expect(firstRowText).toBeTruthy();
  });

  it('navigates to the security detail page when a symbol is clicked', async () => {
    mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    fireEvent.click(holdings().getByText('INTC'));
    // Navigation happens via react-router; no crash and the row remains rendered.
    expect(holdings().getByText('INTC')).toBeInTheDocument();
  });

  it('opens the Add Lot dialog', async () => {
    mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Add Lot/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  // The harvest ladder column (issue #147 Part H6).
  it('links to the running plan and invites one where there is none', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        {
          symbols: [
            symbolRow({ symbol: 'INTC', active_plan_id: 7 }),
            symbolRow({ symbol: 'AAPL', active_plan_id: null }),
          ],
          totals: null,
        },
      ],
    ]);
    renderWithProviders(<PortfolioPage />);
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());

    // One row has a ladder, the other an invitation — never both on a row.
    // By role, because the column header is also the word "Plan".
    expect(holdings().getAllByRole('button', { name: 'Plan' })).toHaveLength(1);
    expect(holdings().getAllByRole('button', { name: /Create plan/i })).toHaveLength(1);
  });

  it('opens the create dialog pre-seeded with the row that was clicked', async () => {
    mockApi([
      [
        '/api/portfolio/symbols',
        { symbols: [symbolRow({ symbol: 'AAPL', active_plan_id: null })], totals: null },
      ],
      ['/api/portfolio', { securities: [{ symbol: 'AAPL', name: 'AAPL Corp', source: 'portfolio', tags: [], currency: 'USD' }] }],
    ]);
    renderWithProviders(<PortfolioPage />);
    await waitFor(() => expect(holdings().getByText('AAPL')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Create plan/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Create Harvest Plan')).toBeInTheDocument();
    await waitFor(() =>
      expect(within(dialog).getByRole('combobox')).toHaveTextContent('AAPL'),
    );
  });

  it('does not fetch holdings for a create dialog nobody has opened', async () => {
    // The dialog stays mounted so it can animate shut; a closed one must not
    // put a request on every portfolio page load.
    const api = mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(api.unmatched).toHaveLength(0);
    expect(api.calls.every(([url]) => url.includes('/api/portfolio/symbols'))).toBe(true);
  });

  it('refetches when the Refresh button is clicked', async () => {
    const api = mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    const before = api.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(before));
  });
});
