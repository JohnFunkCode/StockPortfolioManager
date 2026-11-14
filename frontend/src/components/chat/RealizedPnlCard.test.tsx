import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import RealizedPnlCard from './RealizedPnlCard';
import { mockApi, realizedResponse, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('RealizedPnlCard', () => {
  it("shows the caller's realized P/L for the real book, opening on Year", async () => {
    const api = mockApi([['/api/portfolio/realized', realizedResponse()]]);
    renderWithProviders(<RealizedPnlCard />);
    await waitFor(() => expect(screen.getByTestId('realized-table')).toBeInTheDocument());

    expect(screen.getByTestId('realized-pnl-card')).toBeInTheDocument();
    expect(screen.getByText('Your realized P/L')).toBeInTheDocument();
    expect(screen.getByTestId('realized-pnl-chart')).toBeInTheDocument();
    // No book is ever named: the card has no props to name one with.
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0][0]).toContain('period=year');
    expect(api.calls[0][0]).not.toContain('book_id');
  });

  it('says it is loading while the request is in flight', () => {
    mockApi([['/api/portfolio/realized', () => new Promise<object>(() => {})]]);
    renderWithProviders(<RealizedPnlCard />);
    expect(screen.getByText(/Loading realized P\/L/)).toBeInTheDocument();
  });

  it('shows an error when the request fails', async () => {
    mockApi([['/api/portfolio/realized', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<RealizedPnlCard />);
    await waitFor(() => expect(screen.getByText(/Couldn't load realized P\/L/)).toBeInTheDocument());
  });

  it('explains an empty period', async () => {
    mockApi([['/api/portfolio/realized', realizedResponse([])]]);
    renderWithProviders(<RealizedPnlCard />);
    await waitFor(() => expect(screen.getByText('No sales in the last 365 days')).toBeInTheDocument());
  });
});
