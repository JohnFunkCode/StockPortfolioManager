import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const useSupportConfluenceMock = vi.fn();
vi.mock('../../hooks/useSecurities', () => ({
  useSupportConfluence: (...args: unknown[]) => useSupportConfluenceMock(...args),
}));

import SupportConfluenceCard from './SupportConfluenceCard';

const ZONE_95 = {
  zone_low: 94.5,
  zone_high: 95.5,
  center: 95.0,
  distance_pct: -5.0,
  score: 3.8,
  method_count: 4,
  contributors: [
    { method: 'gamma_wall', level: 95.0, weight: 1.0, detail: 'gamma wall' },
    { method: 'volume_profile', level: 95.2, weight: 0.9, detail: 'point of control' },
    { method: 'anchored_vwap', level: 94.8, weight: 0.9, detail: 'earnings AVWAP' },
    { method: 'atr_bands', level: 94.5, weight: 0.5, detail: 'chandelier stop' },
  ],
};

const ZONE_105 = {
  zone_low: 105.0,
  zone_high: 105.0,
  center: 105.0,
  distance_pct: 5.0,
  score: 1.0,
  method_count: 1,
  contributors: [
    { method: 'gex_profile', level: 105.0, weight: 1.0, detail: 'call wall' },
  ],
};

const RESPONSE = {
  symbol: 'NVDA',
  price: 100.0,
  tolerance_pct: 1.0,
  methods_available: ['gamma_wall', 'volume_profile', 'anchored_vwap', 'atr_bands', 'gex_profile'],
  methods_failed: [],
  support_zones: [ZONE_95],
  resistance_zones: [ZONE_105],
  strongest_support: ZONE_95,
  interpretation: 'Strongest support zone 94.5–95.5 (center 95.0, -5.0% from price).',
};

afterEach(() => {
  cleanup();
  useSupportConfluenceMock.mockReset();
});

describe('SupportConfluenceCard', () => {
  it('renders zones with scores, method counts, and contributors', () => {
    useSupportConfluenceMock.mockReturnValue({
      data: RESPONSE, isLoading: false, error: null, refetch: vi.fn(),
    });

    render(<SupportConfluenceCard ticker="NVDA" />);

    const card = screen.getByTestId('support-confluence-card');
    expect(card.textContent).toContain('Support Confluence');
    expect(card.textContent).toContain('Price $100.00');
    expect(screen.getByTestId('strongest-support-chip').textContent)
      .toContain('$95.00');

    const support = screen.getByTestId('confluence-zones-support');
    expect(support.textContent).toContain('$94.50–$95.50');
    expect(support.textContent).toContain('3.80');           // score
    expect(support.textContent).toContain('gamma_wall@95.00'); // contributors line
    expect(support.textContent).toContain('-5.0%');

    const resistance = screen.getByTestId('confluence-zones-resistance');
    expect(resistance.textContent).toContain('$105.00–$105.00');
    expect(resistance.textContent).toContain('+5.0%');

    expect(card.textContent).toContain('Strongest support zone 94.5–95.5');
  });

  it('shows a loading spinner while fetching', () => {
    useSupportConfluenceMock.mockReturnValue({
      data: undefined, isLoading: true, error: null, refetch: vi.fn(),
    });
    render(<SupportConfluenceCard ticker="NVDA" />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows an error state with a working retry button', () => {
    const refetch = vi.fn();
    useSupportConfluenceMock.mockReturnValue({
      data: undefined, isLoading: false, error: new Error('boom'), refetch,
    });
    render(<SupportConfluenceCard ticker="NVDA" />);

    expect(screen.getByText('boom')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('treats a server-side error payload as an error state', () => {
    useSupportConfluenceMock.mockReturnValue({
      data: { symbol: 'NVDA', error: 'Could not fetch price: timeout' },
      isLoading: false, error: null, refetch: vi.fn(),
    });
    render(<SupportConfluenceCard ticker="NVDA" />);
    expect(screen.getByText(/Could not fetch price/)).toBeInTheDocument();
    expect(screen.queryByTestId('confluence-zones-support')).toBeNull();
  });

  it('renders a warning listing failed level sources', () => {
    useSupportConfluenceMock.mockReturnValue({
      data: {
        ...RESPONSE,
        methods_failed: ['gamma_wall', 'gex_profile'],
        interpretation: RESPONSE.interpretation + ' Sources unavailable: gamma_wall, gex_profile.',
      },
      isLoading: false, error: null, refetch: vi.fn(),
    });
    render(<SupportConfluenceCard ticker="NVDA" />);

    const warning = screen.getByTestId('confluence-methods-failed');
    expect(warning.textContent).toContain('gamma_wall, gex_profile');
  });

  it('renders empty-state text when a side has no zones', () => {
    useSupportConfluenceMock.mockReturnValue({
      data: { ...RESPONSE, resistance_zones: [] },
      isLoading: false, error: null, refetch: vi.fn(),
    });
    render(<SupportConfluenceCard ticker="NVDA" />);
    expect(screen.getByTestId('confluence-zones-resistance').textContent)
      .toContain('No resistance zones');
  });
});
