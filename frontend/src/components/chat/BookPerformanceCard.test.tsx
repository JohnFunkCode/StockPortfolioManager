import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import BookPerformanceCard from './BookPerformanceCard';
import { bookPerformance, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('BookPerformanceCard', () => {
  it("shows the caller's real book against SPY, naming no book", async () => {
    const api = mockApi([['/api/books/performance', bookPerformance()]]);
    renderWithProviders(<BookPerformanceCard />);
    await waitFor(() => expect(screen.getByTestId('performance-chart')).toBeInTheDocument());
    expect(screen.getByTestId('book-performance-card')).toBeInTheDocument();
    expect(screen.getByText('Your performance')).toBeInTheDocument();
    expect(screen.getByText('21.00%')).toBeInTheDocument();
    // The chat is about the one book: no link off to the Compare view.
    expect(screen.queryByRole('link', { name: 'Compare books' })).toBeNull();
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0][0]).toBe('/api/books/performance?period=year');
  });

  it('says it is loading while the request is in flight', () => {
    mockApi([['/api/books/performance', () => new Promise<object>(() => {})]]);
    renderWithProviders(<BookPerformanceCard />);
    expect(screen.getByText(/Loading performance/)).toBeInTheDocument();
  });

  it('shows an error when the request fails', async () => {
    mockApi([['/api/books/performance', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<BookPerformanceCard />);
    await waitFor(() => expect(screen.getByText(/Couldn't load performance/)).toBeInTheDocument());
  });
});
