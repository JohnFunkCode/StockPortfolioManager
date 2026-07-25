import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import PriceChartCard from './PriceChartCard';
import { indicatorRows, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PriceChartCard', () => {
  it('renders the price chart once technicals load', async () => {
    mockApi([['/technicals', { ticker: 'INTC', indicators: indicatorRows(60) }]]);
    const { container } = renderWithProviders(<PriceChartCard ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText(/price & moving averages/i)).toBeInTheDocument(),
    );
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBeGreaterThan(0);
  });

  it('shows an info alert when there is no history', async () => {
    mockApi([['/technicals', { ticker: 'INTC', indicators: [] }]]);
    renderWithProviders(<PriceChartCard ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText(/No price history available/i)).toBeInTheDocument(),
    );
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([['/technicals', () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<PriceChartCard ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load INTC price history/i)).toBeInTheDocument(),
    );
  });
});
