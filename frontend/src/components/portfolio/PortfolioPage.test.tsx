import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import PortfolioPage from './PortfolioPage';
import {
  cashEntry,
  cashLedger,
  emptyCashLedger,
  lotRow,
  mockApi,
  portfolioTotals,
  realizedResponse,
  realizedSymbol,
  renderWithProviders,
  symbolRow,
} from '../../testUtils';

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
    ['/api/books', { books: [] }],
    // The Realized section's own request (#235): no sales unless a test says so.
    ['/api/portfolio/realized', realizedResponse([])],
    ...routes,
    // The Cash section's own request (#431): no opening balance unless a test
    // says so. After `routes`, so a test's own cash route wins.
    ['/api/portfolio/cash', emptyCashLedger()],
  ]);
  renderWithProviders(<PortfolioPage />);
  return api;
}

// The allocation chart above the table labels its x axis with the same tickers
// (issue #147 Part A), so a page-wide getByText('INTC') is ambiguous. Symbol
// lookups scope to the holdings table; the chart has its own tests.
// The Realized section below has a table of its own, so it is found by id.
const holdings = () => within(screen.getByTestId('holdings-table'));

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

    it('says why an earlier-session 1d figure is stale', async () => {
      mountRows([symbolRow({
        return_1d_display: -2.5, return_1d_stale: true, quote_earlier_session: true,
      })]);
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      fireEvent.mouseOver(within(screen.getByTestId('one-day-INTC')).getByLabelText('stale'));
      expect(await screen.findByText(/No trade in the current session yet/)).toBeInTheDocument();
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
      [/\/api\/portfolio$/, { securities: [{ symbol: 'AAPL', name: 'AAPL Corp', source: 'portfolio', tags: [], currency: 'USD' }] }],
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
    expect(
      api.calls.every(
        ([url]) =>
          url.includes('/api/portfolio/symbols') ||
          url.includes('/api/portfolio/realized') ||
          url.includes('/api/portfolio/cash') ||
          url.includes('/api/books'),
      ),
    ).toBe(true);
  });

  it('refetches when the Refresh button is clicked', async () => {
    const api = mount();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    const before = api.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(before));
  });
});

describe('PortfolioPage books (#431)', () => {
  const REAL = { book_id: 1, name: 'Real', kind: 'real', starting_cash: 0, created_at: '2026-11-01T00:00:00Z', archived_at: null };
  const PAPER = { book_id: 7, name: 'Momentum', kind: 'paper', starting_cash: 10000, created_at: '2026-11-02T00:00:00Z', archived_at: null };
  const totals = null;

  function mountBooks(options: { route?: string; paperRows?: object | null } = {}) {
    const paper =
      options.paperRows === undefined
        ? { symbols: [symbolRow({ symbol: 'NVDA', lots: [lotRow({ lot_id: 9, symbol: 'NVDA', account: 'Sim' })] })], totals }
        : options.paperRows;
    const api = mockApi([
      [/\/api\/books\/7\/archive$/, { ...PAPER, archived_at: '2026-11-14T00:00:00Z' }],
      ['/api/books', { books: [REAL, PAPER] }],
      [/\/api\/portfolio\/symbols\?book_id=7$/, paper ?? { __status: 404, error: 'book 7 not found' }],
      [/\/api\/portfolio\/symbols$/, { symbols: [symbolRow({ symbol: 'INTC' })], totals }],
    ]);
    renderWithProviders(<PortfolioPage />, { route: options.route ?? '/' });
    return api;
  }

  const urls = (api: { calls: Array<[string, RequestInit | undefined]> }) => api.calls.map(([url]) => url);

  it('shows the real book by default and sends no book id', async () => {
    const api = mountBooks();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(await screen.findByRole('button', { name: 'Momentum' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Real' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /Add Lot/i })).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(urls(api).some((url) => url.includes('book_id'))).toBe(false);
  });

  it('switches to a paper book: banner, no Add Lot, no Plan column, no lot actions', async () => {
    const api = mountBooks();
    fireEvent.click(await screen.findByRole('button', { name: 'Momentum' }));

    await waitFor(() => expect(holdings().getByText('NVDA')).toBeInTheDocument());
    expect(urls(api)).toContain('/api/portfolio/symbols?book_id=7');
    const banner = screen.getByRole('status');
    expect(within(banner).getByText('Paper book: Momentum')).toBeInTheDocument();
    expect(within(banner).getByText(/Nothing here is a real holding/)).toBeInTheDocument();
    expect(within(banner).getByText(/\$10,000\.00/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Lot/i })).not.toBeInTheDocument();
    expect(screen.queryByText('Plan')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Create plan/i })).not.toBeInTheDocument();

    // Open the symbol's lots: the account is a column, and nothing is editable.
    fireEvent.click(document.querySelector('svg[data-testid="KeyboardArrowRightIcon"]')!.closest('button')!);
    expect(await screen.findByText('Account')).toBeInTheDocument();
    expect(screen.getByText('Sim')).toBeInTheDocument();
    expect(screen.queryByText('Actions')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Edit lot')).not.toBeInTheDocument();
  });

  it('keeps the Account column and the lot actions in the real book', async () => {
    mountBooks();
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    fireEvent.click(document.querySelector('svg[data-testid="KeyboardArrowRightIcon"]')!.closest('button')!);
    expect(await screen.findByText('Account')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
    expect(screen.getByLabelText('Edit lot')).toBeInTheDocument();
  });

  it('opens on the book named in the URL, and goes back to Real', async () => {
    const api = mountBooks({ route: '/?book=7' });
    await waitFor(() => expect(holdings().getByText('NVDA')).toBeInTheDocument());
    expect(urls(api).filter((url) => url.endsWith('/api/portfolio/symbols'))).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('says an empty paper book is empty', async () => {
    mountBooks({ route: '/?book=7', paperRows: { symbols: [], totals } });
    expect(await screen.findByText('This paper book is empty')).toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('keeps the selector on screen when the book cannot be loaded', async () => {
    mountBooks({ route: '/?book=7', paperRows: null });
    expect(await screen.findByText('book 7 not found')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
  });

  it('ignores a book parameter that is not a number', async () => {
    const api = mountBooks({ route: '/?book=abc' });
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(urls(api).some((url) => url.includes('book_id'))).toBe(false);
  });

  it('returns to the real book after archiving the paper book', async () => {
    const api = mountBooks({ route: '/?book=7' });
    await waitFor(() => expect(holdings().getByText('NVDA')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Archive book' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive' }));

    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    const call = api.calls.find(([url]) => url.endsWith('/api/books/7/archive'));
    expect(call?.[1]?.method).toBe('POST');
  });

  it('still shows the Real chip and the holdings when the book list fails', async () => {
    mockApi([
      ['/api/books', { __status: 500, error: 'boom' }],
      ['/api/portfolio/symbols', { symbols: [symbolRow({ symbol: 'INTC' })], totals }],
    ]);
    renderWithProviders(<PortfolioPage />);
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Real' })).toBeInTheDocument();
  });

  // A list that failed can't say what book 1 is, so the page doesn't call it
  // paper. It stays read-only, because it can't say the book is real either.
  it('does not call a named book paper when the book list fails', async () => {
    mockApi([
      ['/api/books', { __status: 500, error: 'boom' }],
      [/\/api\/portfolio\/symbols\?book_id=1$/, { symbols: [symbolRow({ symbol: 'INTC' })], totals }],
    ]);
    renderWithProviders(<PortfolioPage />, { route: '/?book=1' });
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByText(/Paper book/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Lot/i })).not.toBeInTheDocument();
    expect(screen.queryByText('Plan')).not.toBeInTheDocument();
  });

  it('treats the real book named by its own id as the real book', async () => {
    mockApi([
      ['/api/books', { books: [REAL, PAPER] }],
      [/\/api\/portfolio\/symbols\?book_id=1$/, { symbols: [symbolRow({ symbol: 'INTC' })], totals }],
    ]);
    renderWithProviders(<PortfolioPage />, { route: '/?book=1' });
    await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
    expect(await screen.findByRole('button', { name: /Add Lot/i })).toBeInTheDocument();
    expect(screen.getByText('Plan')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows the banner for a book the loaded list does not hold', async () => {
    mockApi([
      ['/api/books', { books: [REAL] }],
      [/\/api\/portfolio\/symbols\?book_id=7$/, { symbols: [symbolRow({ symbol: 'NVDA' })], totals }],
    ]);
    renderWithProviders(<PortfolioPage />, { route: '/?book=7' });
    await waitFor(() => expect(holdings().getByText('NVDA')).toBeInTheDocument());
    expect(await screen.findByRole('status')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Lot/i })).not.toBeInTheDocument();
  });

  describe('stacked sections (#235, decision 7)', () => {
    it('shows the summary strip, then Holdings, Realized and Cash', async () => {
      mount();
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());

      const strip = within(screen.getByTestId('book-summary-strip'));
      expect(strip.getByText('Book value')).toBeInTheDocument();
      expect(strip.getByText('$630.00')).toBeInTheDocument();
      expect(strip.getByText('Unrealized')).toBeInTheDocument();

      const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
      expect(headings).toEqual(['Holdings', 'Realized', 'Cash and orders']);
      // The jump links land on the sections.
      expect(document.getElementById('holdings')).toHaveTextContent('Holdings');
      expect(document.getElementById('realized')).toContainElement(screen.getByTestId('realized-pnl'));
      expect(document.getElementById('cash')).toContainElement(screen.getByTestId('cash-ledger'));
    });

    it('puts the cash balance in the strip from the Cash section\'s own request', async () => {
      const api = mount([['/api/portfolio/cash', cashLedger()]]);
      await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());

      const strip = within(screen.getByTestId('book-summary-strip'));
      expect(strip.getByText('Cash')).toBeInTheDocument();
      expect(strip.getByText('$1,250.00')).toBeInTheDocument();
      // Shown beside the holdings, never added to them: the first figure is
      // renamed so it can't be read as including the cash.
      expect(strip.getByText('Holdings value')).toBeInTheDocument();
      expect(strip.getByText('$630.00')).toBeInTheDocument();
      expect(strip.queryByText('Book value')).toBeNull();
      // One request serves the strip and the section.
      expect(api.calls.filter(([url]) => url.includes('/api/portfolio/cash'))).toHaveLength(1);
    });

    it('keeps Holdings and Realized when the cash request fails', async () => {
      mount([['/api/portfolio/cash', () => ({ __status: 500, error: 'boom' })]]);
      await waitFor(() => expect(screen.getByText(/Couldn't load cash/)).toBeInTheDocument());
      expect(holdings().getByText('INTC')).toBeInTheDocument();
      const strip = within(screen.getByTestId('book-summary-strip'));
      expect(strip.queryByText('Cash')).toBeNull();
      expect(strip.getByText('Book value')).toBeInTheDocument();
    });

    it('lets the real book take cash entries and a paper book none', async () => {
      const REAL_BOOK = { book_id: 1, name: 'Real', kind: 'real', starting_cash: 0, created_at: '2026-11-01T00:00:00Z', archived_at: null };
      const PAPER_BOOK = { book_id: 7, name: 'Momentum', kind: 'paper', starting_cash: 5000, created_at: '2026-11-02T00:00:00Z', archived_at: null };
      const paperLedger = cashLedger(
        [cashEntry({ entry_id: 9, kind: 'opening_balance', entry_date: '2026-11-02', amount: 5000, balance_after: 5000 })],
        { book_kind: 'paper', balance: 5000, opening_balance: 5000, opening_date: '2026-11-02' },
      );
      const api = mockApi([
        ['/api/books', { books: [REAL_BOOK, PAPER_BOOK] }],
        [/\/api\/portfolio\/cash\?book_id=7$/, paperLedger],
        [/\/api\/portfolio\/cash$/, cashLedger()],
        ['/api/portfolio/symbols', { symbols: [], totals: null }],
        ['/api/portfolio/realized', realizedResponse([])],
      ]);
      renderWithProviders(<PortfolioPage />);
      await waitFor(() => expect(screen.getByTestId('cash-table')).toBeInTheDocument());
      expect(screen.getByRole('button', { name: 'Add deposit or withdrawal' })).toBeInTheDocument();

      fireEvent.click(await screen.findByRole('button', { name: 'Momentum' }));
      await waitFor(() => expect(within(screen.getByTestId('cash-ledger')).getAllByText('$5,000.00').length).toBeGreaterThan(0));
      expect(screen.queryByRole('button', { name: 'Add deposit or withdrawal' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Change opening balance' })).toBeNull();
      expect(api.calls.map(([url]) => url)).toContain('/api/portfolio/cash?book_id=7');
    });

    it("puts the last year's realized P/L in the strip from the section's own request", async () => {
      const api = mockApi([
        ['/api/portfolio/symbols', { symbols: [symbolRow()], totals: null }],
        ['/api/books', { books: [] }],
        [
          '/api/portfolio/realized',
          realizedResponse([realizedSymbol()], {
            totals: { ...realizedResponse().totals, realized_pnl: 1234.5 },
          }),
        ],
      ]);
      renderWithProviders(<PortfolioPage />);
      await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());

      const strip = within(screen.getByTestId('book-summary-strip'));
      expect(strip.getByText('Realized, last year')).toBeInTheDocument();
      expect(strip.getByText('$1,234.50')).toBeInTheDocument();
      // The strip and the section both want Year: one request, not two.
      expect(api.calls.filter(([url]) => url.includes('/api/portfolio/realized'))).toHaveLength(1);
    });

    it("keeps the strip on the last year when the section's period changes", async () => {
      mockApi([
        ['/api/portfolio/symbols', { symbols: [symbolRow()], totals: null }],
        ['/api/books', { books: [] }],
        [
          /realized\?period=all/,
          realizedResponse([realizedSymbol()], {
            period: 'all',
            totals: { ...realizedResponse().totals, realized_pnl: 999 },
          }),
        ],
        [
          '/api/portfolio/realized',
          realizedResponse([realizedSymbol()], {
            totals: { ...realizedResponse().totals, realized_pnl: 111 },
          }),
        ],
      ]);
      renderWithProviders(<PortfolioPage />);
      const strip = () => within(screen.getByTestId('book-summary-strip'));
      await waitFor(() => expect(strip().getByText('$111.00')).toBeInTheDocument());

      fireEvent.change(screen.getByLabelText('Realized period'), { target: { value: 'all' } });

      await waitFor(() =>
        expect(within(screen.getByTestId('realized-pnl')).getByText('$999.00')).toBeInTheDocument(),
      );
      expect(strip().getByText('$111.00')).toBeInTheDocument();
      expect(strip().queryByText('$999.00')).toBeNull();
    });

    it('shows Holdings without waiting for Realized', async () => {
      mockApi([
        ['/api/portfolio/symbols', { symbols: [symbolRow()], totals: null }],
        ['/api/books', { books: [] }],
        ['/api/portfolio/realized', () => new Promise<object>(() => {})],
      ]);
      renderWithProviders(<PortfolioPage />);
      await waitFor(() => expect(holdings().getByText('INTC')).toBeInTheDocument());
      expect(screen.getByText(/Loading realized P\/L/)).toBeInTheDocument();
      expect(screen.queryByText('Realized, last year')).toBeNull();
    });

    it('keeps Holdings when Realized fails, and says which part failed', async () => {
      mockApi([
        ['/api/portfolio/symbols', { symbols: [symbolRow()], totals: portfolioTotals() }],
        ['/api/books', { books: [] }],
        ['/api/portfolio/realized', () => ({ __status: 500, error: 'boom' })],
      ]);
      renderWithProviders(<PortfolioPage />);
      await waitFor(() => expect(screen.getByText(/Couldn't load realized P\/L/)).toBeInTheDocument());
      expect(holdings().getByText('INTC')).toBeInTheDocument();
      // The strip leaves the failed figure out and keeps the ones it has
      // (#554 review): no zero, dash or N/A stands in for it.
      const strip = within(screen.getByTestId('book-summary-strip'));
      expect(strip.queryByText('Realized, last year')).toBeNull();
      expect(strip.queryByText(/N\/A|—|\$0\.00/)).toBeNull();
      expect(strip.getByText('Book value')).toBeInTheDocument();
      expect(strip.getByRole('link', { name: 'Realized' })).toBeInTheDocument();
    });

    it('shows what a book with no open lots has realized', async () => {
      // Fully sold out: the holdings are empty and the sales are not.
      mockApi([
        ['/api/portfolio/symbols', { symbols: [], totals: null }],
        ['/api/books', { books: [] }],
        ['/api/portfolio/realized', realizedResponse()],
      ]);
      renderWithProviders(<PortfolioPage />);
      await waitFor(() => expect(screen.getByTestId('realized-row-INTC')).toBeInTheDocument());
      expect(screen.getByText('Your portfolio is empty')).toBeInTheDocument();
      expect(screen.queryByText('Book value')).toBeNull();
    });

    it("asks for the selected book's realized P/L", async () => {
      const api = mockApi([
        [/\/api\/portfolio\/symbols\?book_id=7$/, { symbols: [], totals: null }],
        ['/api/books', { books: [] }],
        ['/api/portfolio/realized', realizedResponse()],
      ]);
      renderWithProviders(<PortfolioPage />, { route: '/?book=7' });
      await waitFor(() => expect(screen.getByTestId('realized-row-INTC')).toBeInTheDocument());
      const realized = api.calls.filter(([url]) => url.includes('/api/portfolio/realized'));
      expect(realized.map(([url]) => url)).toEqual([
        expect.stringContaining('period=year&book_id=7'),
      ]);
    });
  });
});
