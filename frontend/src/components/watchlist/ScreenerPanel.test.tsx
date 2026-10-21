/** ScreenerPanel (moved from the retired Securities page, #224). */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import ScreenerPanel from './ScreenerPanel';
import { mockApi, renderWithProviders } from '../../testUtils';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const RESULTS = {
  results: [
    { symbol: 'INTC', name: 'Intel', last_close: 21.456, rsi: 27.6, news_sentiment: 'negative' },
    { symbol: 'KO', name: 'Coca-Cola', last_close: 60, rsi: null },
  ],
  count: 2,
};

afterEach(() => {
  vi.unstubAllGlobals();
  mockNavigate.mockReset();
});

describe('ScreenerPanel', () => {
  it('does not scan until a preset is chosen', () => {
    const api = mockApi([['/api/securities/screen', RESULTS]]);
    renderWithProviders(<ScreenerPanel open />);
    expect(screen.getByText('Technical Screener (from cached OHLCV data)')).toBeInTheDocument();
    expect(screen.queryByText('Clear')).toBeNull();
    expect(api.calls).toHaveLength(0);
  });

  it('scans with the preset params and opens a result', async () => {
    const api = mockApi([['/api/securities/screen', RESULTS]]);
    renderWithProviders(<ScreenerPanel open />);
    fireEvent.click(screen.getByText('Oversold (RSI < 30)'));

    expect(await screen.findByText('2 matches — click to navigate')).toBeInTheDocument();
    expect(api.calls[0][0]).toContain('rsi_max=30');
    expect(screen.getByText('INTC · negative · RSI 28 · $21.46')).toBeInTheDocument();
    expect(screen.getByText('KO · $60.00')).toBeInTheDocument();

    fireEvent.click(screen.getByText('KO · $60.00'));
    expect(mockNavigate).toHaveBeenCalledWith('/securities/KO');
  });

  it('shows a scanning state, and Clear drops the results', async () => {
    mockApi([['/api/securities/screen', { results: [RESULTS.results[0]], count: 1 }]]);
    renderWithProviders(<ScreenerPanel open />);
    fireEvent.click(screen.getByText('News: Negative'));
    expect(screen.getByText('Scanning...')).toBeInTheDocument();
    expect(await screen.findByText('1 match — click to navigate')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Clear'));
    await waitFor(() => expect(screen.queryByText(/match/)).toBeNull());
    expect(screen.queryByText('Clear')).toBeNull();
  });

  it('toggles a preset off by clicking it again', async () => {
    mockApi([['/api/securities/screen', RESULTS]]);
    renderWithProviders(<ScreenerPanel open />);
    fireEvent.click(screen.getByText('Above MA200'));
    await screen.findByText('2 matches — click to navigate');
    fireEvent.click(screen.getByText('Above MA200'));
    await waitFor(() => expect(screen.queryByText('Clear')).toBeNull());
  });
});
