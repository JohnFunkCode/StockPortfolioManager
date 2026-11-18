import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import CompareBooksPage from './CompareBooksPage';
import { bookPerformance, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const COMPARE = '/api/books/performance/compare';

function compare(books: object[] = [
  bookPerformance(),
  bookPerformance({ book_id: 7, name: 'Momentum', kind: 'paper', twr: -0.02, vs_benchmark: -0.12 }),
]) {
  return { period: 'year', start_date: '2025-11-17', end_date: '2026-11-17', books };
}

function mount(payload: object = compare()) {
  const api = mockApi([[COMPARE, payload]]);
  renderWithProviders(<CompareBooksPage />);
  return api;
}

describe('CompareBooksPage', () => {
  it('says it is loading, then shows every book against SPY', async () => {
    const api = mount();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('compare-table')).toBeInTheDocument());
    expect(api.calls[0][0]).toBe('/api/books/performance/compare?period=year');

    const real = within(screen.getByTestId('compare-row-1'));
    expect(real.getByRole('link', { name: 'Real' })).toHaveAttribute('href', '/');
    expect(real.getByText('$1,210.00')).toBeInTheDocument();
    expect(real.getByText('21.00%')).toBeInTheDocument();
    expect(real.getByText('11.00%')).toBeInTheDocument();
    expect(real.getByText('$35.00')).toBeInTheDocument();

    const paper = within(screen.getByTestId('compare-row-7'));
    expect(paper.getByRole('link', { name: 'Momentum' })).toHaveAttribute('href', '/?book=7');
    expect(paper.getByText('paper')).toBeInTheDocument();
    expect(paper.getByText('-12.00%')).toBeInTheDocument();

    // One line a book, and SPY once.
    expect(document.querySelectorAll('path.book-line')).toHaveLength(2);
    expect(document.querySelectorAll('path.benchmark-line')).toHaveLength(1);
    expect(screen.getByText(/Books are never added together/)).toBeInTheDocument();
  });

  it('shows an error when the request fails', async () => {
    mockApi([[COMPARE, () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<CompareBooksPage />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load the books' performance/)).toBeInTheDocument(),
    );
  });

  it('marks a book whose cash went below zero as unreliable', async () => {
    mount(compare([bookPerformance({ cash_negative_count: 1 })]));
    await waitFor(() => expect(screen.getByTestId('compare-table')).toBeInTheDocument());
    expect(screen.getByText('unreliable')).toBeInTheDocument();
  });

  it('draws no SPY line, and says so, when SPY could not be read', async () => {
    mount(compare([bookPerformance({ benchmark_available: false, vs_benchmark: null })]));
    await waitFor(() => expect(screen.getByTestId('compare-table')).toBeInTheDocument());
    expect(document.querySelectorAll('path.benchmark-line')).toHaveLength(0);
    expect(screen.getByText(/SPY's prices could not be read/)).toBeInTheDocument();
  });

  it('lists a book with no history yet without a value', async () => {
    mount(compare([bookPerformance({ series: [], twr: null, latest: null, book_id: null })]));
    await waitFor(() => expect(screen.getByTestId('compare-table')).toBeInTheDocument());
    expect(within(screen.getByTestId('compare-row-real')).getByText('—')).toBeInTheDocument();
    expect(screen.getByText('No valued sessions to chart yet.')).toBeInTheDocument();
  });

  it('has its own range control', async () => {
    const api = mount();
    await waitFor(() => expect(screen.getByTestId('compare-table')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Compare range'), { target: { value: 'quarter' } });
    await waitFor(() =>
      expect(api.calls.map(([url]) => url)).toContain('/api/books/performance/compare?period=quarter'),
    );
  });
});
