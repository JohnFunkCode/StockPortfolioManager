import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';

import SecuritiesPage from './SecuritiesPage';
import { mockApi, renderWithProviders, securityRow } from '../../testUtils';

const SECURITIES = {
  securities: [
    securityRow('INTC', { source: 'portfolio', tags: ['chips'] }),
    securityRow('WMT', { source: 'watchlist', tags: ['retail'] }),
    securityRow('NVDA', { source: 'watchlist', tags: ['chips'] }),
  ],
};

function arm() {
  return mockApi([
    ['/api/securities', SECURITIES],
    ['/news/sentiment-summary', { items: [] }],
  ]);
}

afterEach(() => vi.unstubAllGlobals());

describe('SecuritiesPage', () => {
  it('renders the securities in a grid', async () => {
    arm();
    renderWithProviders(<SecuritiesPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.getByText('WMT')).toBeInTheDocument();
    expect(screen.getByText('NVDA')).toBeInTheDocument();
  });

  it('filters the grid by the search box', async () => {
    arm();
    renderWithProviders(<SecuritiesPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    const search = document.querySelector('input[type="text"]')
      ?? screen.getByRole('textbox');
    fireEvent.change(search as HTMLInputElement, { target: { value: 'WMT' } });
    await waitFor(() => expect(screen.queryByText('INTC')).toBeNull());
    expect(screen.getByText('WMT')).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockApi([
      ['/api/securities', () => ({ __status: 500, error: 'securities down' })],
      ['/news/sentiment-summary', { items: [] }],
    ]);
    renderWithProviders(<SecuritiesPage />);
    await waitFor(() =>
      expect(screen.getByText(/securities down/i)).toBeInTheDocument(),
    );
  });
});
