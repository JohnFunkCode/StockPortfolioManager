import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import PortfolioPage from './PortfolioPage';
import { mockApi, renderWithProviders, symbolRow } from '../../testUtils';

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
    expect(screen.getAllByText('$30.00').length).toBeGreaterThanOrEqual(1);
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
