import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

const useVerticalSpreadMock = vi.fn();
const usePricePollingMock = vi.fn();
vi.mock('../../hooks/useSecurities', () => ({
  useVerticalSpread: (...args: unknown[]) => useVerticalSpreadMock(...args),
}));
vi.mock('../../hooks/useSymbols', () => ({
  usePricePolling: (...args: unknown[]) => usePricePollingMock(...args),
}));

import SpreadPayoffCard from './SpreadPayoffCard';

const SPREAD_RESPONSE = {
  symbol: 'INTC',
  expiration: '2026-08-21',
  kind: 'call',
  debit: 4.94,
  mid_debit: 4.8,
  max_profit: 15.06,
  max_loss: 4.94,
  breakeven: 144.94,
  risk_reward: 3.05,
  warnings: [],
  legs: {
    long: { strike: 140, mid: 9.1, iv: 0.86 },
    short: { strike: 160, mid: 4.3, iv: 0.91 },
  },
};

const PROPS = {
  ticker: 'INTC',
  expiration: '2099-08-21', // far future so DTE is positive in tests forever
  long_strike: 140,
  short_strike: 160,
  kind: 'call',
};

afterEach(() => {
  cleanup();
  useVerticalSpreadMock.mockReset();
  usePricePollingMock.mockReset();
});

describe('SpreadPayoffCard', () => {
  it('renders header chips and the payoff svg on success', () => {
    useVerticalSpreadMock.mockReturnValue({
      data: { ...SPREAD_RESPONSE, expiration: PROPS.expiration },
      isLoading: false,
      error: null,
    });
    usePricePollingMock.mockReturnValue({ data: { ticker: 'INTC', price: 150.25 } });

    render(<SpreadPayoffCard {...PROPS} />);

    expect(screen.getByTestId('spread-payoff-card')).toBeInTheDocument();
    expect(screen.getByTestId('spread-payoff-chart')).toBeInTheDocument();
    const card = screen.getByTestId('spread-payoff-card');
    expect(card.textContent).toContain('4.80'); // mid debit
    expect(card.textContent).toContain('15.06'); // max profit
    expect(card.textContent).toContain('144.94'); // breakeven
  });

  it('shows the loading state while pricing', () => {
    useVerticalSpreadMock.mockReturnValue({ data: undefined, isLoading: true, error: null });
    usePricePollingMock.mockReturnValue({ data: undefined });
    render(<SpreadPayoffCard {...PROPS} />);
    expect(screen.getByText(/Pricing/i)).toBeInTheDocument();
  });

  it('shows an error alert when pricing fails', () => {
    useVerticalSpreadMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('no contracts'),
    });
    usePricePollingMock.mockReturnValue({ data: undefined });
    render(<SpreadPayoffCard {...PROPS} />);
    expect(screen.getByTestId('spread-payoff-error')).toBeInTheDocument();
  });

  it('surfaces liquidity warnings from the service', () => {
    useVerticalSpreadMock.mockReturnValue({
      data: {
        ...SPREAD_RESPONSE,
        expiration: PROPS.expiration,
        warnings: ['wide bid/ask on short leg'],
      },
      isLoading: false,
      error: null,
    });
    usePricePollingMock.mockReturnValue({ data: { ticker: 'INTC', price: 150 } });
    render(<SpreadPayoffCard {...PROPS} />);
    expect(screen.getByTestId('spread-payoff-warning').textContent).toContain('wide bid/ask');
  });
});
