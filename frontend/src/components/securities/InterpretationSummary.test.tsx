import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import InterpretationSummary from './InterpretationSummary';
import { FLOW, RISK, TECH, pending } from './signalsFixtures';
import { makeQueryClient, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const NEWS = {
  symbol: 'INTC', article_count: 2, articles: [],
  sentiment_summary: { overall: 'positive', positive_count: 2, negative_count: 0, neutral_count: 0, scored_count: 2 },
};

function arm(overrides: Record<string, unknown> = {}) {
  return mockApi([
    ['/signals/technical', overrides.tech ?? TECH],
    ['/signals/options-flow', overrides.flow ?? FLOW],
    ['/signals/risk', overrides.risk ?? RISK],
    ['/news', overrides.news ?? NEWS],
  ]);
}

describe('InterpretationSummary', () => {
  it('says nothing while a signal query is still loading', async () => {
    const api = arm({ risk: pending });
    const { container } = renderWithProviders(<InterpretationSummary ticker="INTC" />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThanOrEqual(3));
    expect(container).toBeEmptyDOMElement();
  });

  it('writes one line per signal family once loaded', async () => {
    arm();
    renderWithProviders(<InterpretationSummary ticker="INTC" />);
    expect(await screen.findByText('Interpretation')).toBeInTheDocument();
    expect(screen.getByText(/^Technical picture is broadly bullish/)).toBeInTheDocument();
    expect(screen.getByText(/^Volume is running 2\.5× the average/)).toBeInTheDocument();
    expect(screen.getByText(/minimum trailing stop of 8\.5%/)).toBeInTheDocument();
    expect(await screen.findByText(/News sentiment across 2 recent articles is broadly positive/)).toBeInTheDocument();
  });

  it('does not wait on the live news fetch', async () => {
    arm({ news: pending });
    renderWithProviders(<InterpretationSummary ticker="INTC" />);
    expect(await screen.findByText(/minimum trailing stop/)).toBeInTheDocument();
    expect(screen.queryByText(/News sentiment/)).toBeNull();
  });

  it('renders nothing when no signal produced a line', async () => {
    const api = arm({
      tech: { __status: 500, error: 'x' }, flow: { __status: 500, error: 'x' },
      risk: { __status: 500, error: 'x' }, news: { symbol: 'INTC', article_count: 0, articles: [] },
    });
    const queryClient = makeQueryClient();
    const { container } = renderWithProviders(<InterpretationSummary ticker="INTC" />, { queryClient });
    // Settled: all four requests made and none still in flight.
    await waitFor(() => {
      expect(api.calls.length).toBeGreaterThanOrEqual(4);
      expect(queryClient.isFetching()).toBe(0);
    });
    expect(container).toBeEmptyDOMElement();
  });
});
