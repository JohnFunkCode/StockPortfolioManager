import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import PortfolioCheckPage from './PortfolioCheckPage';
import { checkReport } from './portfolioCheckFixtures';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PortfolioCheckPage', () => {
  it('composes both cards from one request and says what the data is as of', async () => {
    const api = mockApi([['/api/portfolio/check', checkReport()]]);
    renderWithProviders(<PortfolioCheckPage />);
    expect(screen.getByRole('heading', { name: 'Portfolio Check' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('portfolio-check-card')).toBeInTheDocument());
    expect(screen.getByTestId('portfolio-check-ideas-card')).toBeInTheDocument();
    expect(screen.getByText(/Industry signals as of 2026-09; stock-vs-peers window Nov 14, 2025 to Oct 19, 2026/))
      .toBeInTheDocument();
    expect(screen.getByText(/Taxes assume 32% short-term and 15% long-term/)).toBeInTheDocument();
    // Both cards and the as-of line share one react-query key: one request.
    expect(api.calls.filter(([url]) => url.includes('/api/portfolio/check'))).toHaveLength(1);
  });

  it('no longer frames the old sample report', () => {
    mockApi([['/api/portfolio/check', checkReport()]]);
    const { container } = renderWithProviders(<PortfolioCheckPage />);
    expect(container.querySelector('iframe')).toBeNull();
    expect(screen.queryByText(/Sample portfolio/)).not.toBeInTheDocument();
  });
});
