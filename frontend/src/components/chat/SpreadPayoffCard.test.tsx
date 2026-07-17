import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const useVerticalSpreadMock = vi.fn();
const usePricePollingMock = vi.fn();
vi.mock('../../hooks/useSecurities', () => ({
  useVerticalSpread: (...args: unknown[]) => useVerticalSpreadMock(...args),
}));
vi.mock('../../hooks/useSymbols', () => ({
  usePricePolling: (...args: unknown[]) => usePricePollingMock(...args),
}));

// Fake chat context so DirectiveInteractionProvider is live without ChatProvider.
const queueInteractionMock = vi.fn();
const sendMessageMock = vi.fn();
const chatState: {
  queueInteraction: typeof queueInteractionMock;
  sendMessage: typeof sendMessageMock;
  consumedInteractions: Record<string, unknown[]>;
} = {
  queueInteraction: queueInteractionMock,
  sendMessage: sendMessageMock,
  consumedInteractions: {},
};
vi.mock('../../chat/ChatContext', () => ({
  useChatOptional: () => chatState,
}));

import SpreadPayoffCard from './SpreadPayoffCard';
import { DirectiveInteractionProvider } from '../../chat/DirectiveInteractions';

// Server-computed curves (issue #79) — the card only draws these arrays.
const CURVES = {
  prices: [120, 140, 150, 160, 180],
  expiry: [-4.8, -4.8, 5.2, 15.2, 15.2],
  now: [-3.1, -1.2, 3.4, 8.9, 12.6],
  params: { T: 45 / 365, r: 0.045, spot: 150, debit: 4.8 },
};

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
  curves: CURVES,
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
  queueInteractionMock.mockReset();
  sendMessageMock.mockReset();
  chatState.consumedInteractions = {};
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

  it('is display-only without an interaction provider', () => {
    useVerticalSpreadMock.mockReturnValue({
      data: { ...SPREAD_RESPONSE, expiration: PROPS.expiration },
      isLoading: false,
      error: null,
    });
    usePricePollingMock.mockReturnValue({ data: { ticker: 'INTC', price: 150 } });
    render(<SpreadPayoffCard {...PROPS} />);
    fireEvent.click(screen.getByTestId('spread-payoff-chart'), { clientX: 120 });
    expect(queueInteractionMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('reprice-long')).toBeNull();
  });

  it('shows a distinct error when a priced response has no curves (stale API)', () => {
    const { curves: _omitted, ...withoutCurves } = SPREAD_RESPONSE;
    useVerticalSpreadMock.mockReturnValue({
      data: { ...withoutCurves, expiration: PROPS.expiration },
      isLoading: false,
      error: null,
    });
    usePricePollingMock.mockReturnValue({ data: { ticker: 'INTC', price: 150 } });
    render(<SpreadPayoffCard {...PROPS} />);
    const alert = screen.getByTestId('spread-payoff-error');
    expect(alert.textContent).toContain('no payoff curves');
  });

  it('requests server curves through the hook', () => {
    useVerticalSpreadMock.mockReturnValue({
      data: { ...SPREAD_RESPONSE, expiration: PROPS.expiration },
      isLoading: false,
      error: null,
    });
    usePricePollingMock.mockReturnValue({ data: { ticker: 'INTC', price: 150 } });
    render(<SpreadPayoffCard {...PROPS} />);
    // The card delegates fetching to useVerticalSpread with the card's props;
    // the hook itself owns include_curves: true (asserted here structurally).
    expect(useVerticalSpreadMock).toHaveBeenCalledWith(
      'INTC',
      PROPS.expiration,
      140,
      160,
      'call',
    );
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

describe('SpreadPayoffCard interactions (backchannel)', () => {
  const DIRECTIVE = {
    component: 'spread_payoff',
    componentId: 'inst-1',
    props: PROPS as unknown as Record<string, unknown>,
  };

  function renderInteractive() {
    useVerticalSpreadMock.mockReturnValue({
      data: { ...SPREAD_RESPONSE, expiration: PROPS.expiration },
      isLoading: false,
      error: null,
    });
    usePricePollingMock.mockReturnValue({ data: { ticker: 'INTC', price: 150 } });
    return render(
      <DirectiveInteractionProvider directive={DIRECTIVE} props={DIRECTIVE.props}>
        <SpreadPayoffCard {...PROPS} />
      </DirectiveInteractionProvider>,
    );
  }

  it('clicking the chart queues a select_strike with the instance identity', () => {
    renderInteractive();
    fireEvent.click(screen.getByTestId('spread-payoff-chart'), { clientX: 120 });
    expect(queueInteractionMock).toHaveBeenCalledTimes(1);
    const queued = queueInteractionMock.mock.calls[0][0];
    expect(queued.component).toBe('spread_payoff');
    expect(queued.component_id).toBe('inst-1');
    expect(queued.action).toBe('select_strike');
    expect(Number.isFinite(queued.payload.strike)).toBe(true);
    expect(queued.props).toEqual(DIRECTIVE.props);
  });

  it('selecting a strike reveals reprice chips; clicking one sends a message-mode turn', () => {
    renderInteractive();
    expect(screen.queryByTestId('reprice-long')).toBeNull();
    fireEvent.click(screen.getByTestId('spread-payoff-chart'), { clientX: 120 });

    fireEvent.click(screen.getByTestId('reprice-long'));
    expect(sendMessageMock).toHaveBeenCalledTimes(1);
    const [text, extras] = sendMessageMock.mock.calls[0];
    expect(text).toContain('long leg');
    expect(text).toContain('INTC');
    expect(extras).toHaveLength(1);
    expect(extras[0].action).toBe('reprice_leg');
    expect(extras[0].payload.leg).toBe('long');
    expect(Number.isFinite(extras[0].payload.strike)).toBe(true);
    // Message-mode gestures never touch the context queue.
    expect(queueInteractionMock).toHaveBeenCalledTimes(1); // only the select
  });

  it('locks the card once its gesture has been sent: frozen mark, no clicks, no chips', () => {
    chatState.consumedInteractions = {
      'inst-1': [
        {
          component_id: 'inst-1',
          component: 'spread_payoff',
          action: 'select_strike',
          payload: { strike: 150 },
        },
      ],
    };
    renderInteractive();

    // The answered selection renders as an immutable mark.
    const chart = screen.getByTestId('spread-payoff-chart');
    expect(chart.textContent).toContain('sel 150');
    expect(screen.getByTestId('spread-payoff-card').textContent).toContain(
      'Selection locked',
    );

    // Clicking neither queues a new gesture nor moves the mark.
    fireEvent.click(chart, { clientX: 300 });
    expect(queueInteractionMock).not.toHaveBeenCalled();
    expect(chart.textContent).toContain('sel 150');

    // No live affordances on a historical card.
    expect(screen.queryByTestId('reprice-long')).toBeNull();
    expect(screen.queryByTestId('reprice-short')).toBeNull();
    expect(screen.getByTestId('spread-payoff-card').textContent).not.toContain(
      'Click the chart to select a strike',
    );
  });

  it('mentions chart clickability in the caption when interactive', () => {
    renderInteractive();
    expect(screen.getByTestId('spread-payoff-card').textContent).toContain(
      'Click the chart to select a strike',
    );
  });
});
