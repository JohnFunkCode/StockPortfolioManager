import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import BookPerformance from './BookPerformance';
import { bookPerformance, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const PERFORMANCE = '/api/books/performance';

function mount(payload: object = bookPerformance(), props: { bookId?: number } = {}) {
  const api = mockApi([[PERFORMANCE, payload]]);
  renderWithProviders(<BookPerformance {...props} />);
  return api;
}

const loaded = () => waitFor(() => expect(screen.getByTestId('performance-chart')).toBeInTheDocument());

describe('BookPerformance', () => {
  it('says it is loading while the request is in flight', () => {
    mockApi([[PERFORMANCE, () => new Promise<object>(() => {})]]);
    renderWithProviders(<BookPerformance />);
    expect(screen.getByText(/Loading performance/)).toBeInTheDocument();
  });

  it('shows an error when the request fails', async () => {
    mockApi([[PERFORMANCE, () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<BookPerformance />);
    await waitFor(() => expect(screen.getByText(/Couldn't load performance/)).toBeInTheDocument());
  });

  it("opens on Year for the real book and shows the server's figures", async () => {
    const api = mount();
    await loaded();
    expect(api.calls[0][0]).toBe('/api/books/performance?period=year');
    expect(screen.getByText('Return')).toBeInTheDocument();
    expect(screen.getByText('21.00%')).toBeInTheDocument();
    expect(screen.getByText('10.00%')).toBeInTheDocument();
    expect(screen.getByText('vs SPY')).toBeInTheDocument();
    expect(screen.getByText('11.00%')).toBeInTheDocument();
    expect(screen.getByText('-5.00%')).toBeInTheDocument();
    expect(screen.getByText('Book value, Nov 4, 2026 close')).toBeInTheDocument();
    expect(screen.getByText('$1,210.00')).toBeInTheDocument();
    expect(screen.getByText('Nov 2, 2026 to Nov 4, 2026')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Compare books' })).toHaveAttribute('href', '/books/compare');
  });

  it('shows realized P/L by source, with a total', async () => {
    mount();
    await loaded();
    const table = within(screen.getByTestId('attribution-table'));
    expect(within(screen.getByTestId('attribution-manual')).getByText('Manual')).toBeInTheDocument();
    expect(within(screen.getByTestId('attribution-manual')).getByText('$40.00')).toBeInTheDocument();
    expect(within(screen.getByTestId('attribution-stop')).getByText('Stop')).toBeInTheDocument();
    expect(within(screen.getByTestId('attribution-stop')).getByText('-$5.00')).toBeInTheDocument();
    expect(table.getByText('Total')).toBeInTheDocument();
    expect(table.getByText('$35.00')).toBeInTheDocument();
  });

  it('says what the figures leave out: dividends and splits', async () => {
    mount();
    await loaded();
    expect(screen.getByText(/dividends are not counted/)).toBeInTheDocument();
    expect(screen.getByText(/Stock splits are not adjusted yet \(#391\)/)).toBeInTheDocument();
  });

  it('changes the request when the period changes', async () => {
    const api = mount();
    await loaded();
    fireEvent.change(screen.getByLabelText('Performance period'), { target: { value: 'all' } });
    await waitFor(() =>
      expect(api.calls.map(([url]) => url)).toContain('/api/books/performance?period=all'),
    );
  });

  it('names the book it was given', async () => {
    const api = mount(bookPerformance({ name: 'Momentum', kind: 'paper', book_id: 7 }), { bookId: 7 });
    await loaded();
    expect(api.calls[0][0]).toBe('/api/books/performance?period=year&book_id=7');
  });

  it('marks a return measured over negative cash as unreliable (decision 8)', async () => {
    mount(bookPerformance({ cash_negative_count: 3 }));
    await loaded();
    expect(screen.getByText('Return (unreliable)')).toBeInTheDocument();
    expect(screen.getByTestId('cash-negative-note')).toHaveTextContent('below zero on 3 sessions');
  });

  it('discloses restated sessions, gaps, stale prices and a missing benchmark', async () => {
    mount(
      bookPerformance({
        restated_count: 1,
        last_restated_at: '2026-11-05T02:00:00Z',
        gaps: ['2026-11-03'],
        benchmark_available: false,
        benchmark_return: null,
        vs_benchmark: null,
        latest: { ...bookPerformance().latest, stale_count: 2 },
      }),
    );
    await loaded();
    expect(screen.getByTestId('restated-note')).toHaveTextContent('1 session restated');
    expect(screen.getByTestId('gaps-note')).toHaveTextContent('1 session could not be valued');
    expect(screen.getByTestId('stale-note')).toHaveTextContent('2 holdings were valued at an earlier close');
    expect(screen.getByTestId('benchmark-note')).toBeInTheDocument();
    expect(screen.queryByTestId('cash-negative-note')).toBeNull();
  });

  it('says when the newest close could not be valued', async () => {
    mount(bookPerformance({ latest: { ...bookPerformance().latest, book_value: null, unpriced_count: 1 } }));
    await loaded();
    expect(screen.getByTestId('incomplete-note')).toBeInTheDocument();
    expect(screen.queryByText(/Book value,/)).toBeNull();
  });

  it('explains a book with no history yet', async () => {
    mount(bookPerformance({ series: [], twr: null, latest: null, first_session: null, last_session: null }));
    await waitFor(() => expect(screen.getByTestId('performance-empty')).toBeInTheDocument());
    expect(screen.getByText(/starts at its opening balance/)).toBeInTheDocument();
    expect(screen.queryByTestId('attribution-table')).toBeNull();
  });

  it('says a window with no sales has none', async () => {
    mount(bookPerformance({ attribution: { sources: [], totals: bookPerformance().attribution.totals } }));
    await loaded();
    expect(screen.getByText('No sales in this window.')).toBeInTheDocument();
  });
});
