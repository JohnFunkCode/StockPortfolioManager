import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';

import StopLossCard from './StopLossCard';
import { QueryClient } from '@tanstack/react-query';
import { CONFLUENCE_STOP, mockApi, renderWithProviders, stopLossResponse as response } from '../../testUtils';
import type { SupportConfluenceResponse } from '../../api/securitiesTypes';

const HELD = {
  cost_basis: 150.0, shares: 10, unrealized_pnl_per_share: 30.0, unrealized_pnl_pct: 20.0,
  total_unrealized_pnl: 300.0, pnl_at_technical_stop: 235.4, pnl_at_trailing_stop: 183.0,
  pnl_at_confluence_stop: 186.5,
};

afterEach(() => vi.unstubAllGlobals());

describe('StopLossCard', () => {
  it('shows a spinner while loading', () => {
    mockApi([['/stop-loss', () => new Promise(() => {}) as never]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows an error with a retry', async () => {
    mockApi([['/stop-loss', { __status: 500, error: 'boom' }]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('asks for the caller’s own position with confluence=stop by default', async () => {
    const api = mockApi([['/stop-loss', response()]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    await screen.findByTestId('stop-tile-technical');
    expect(api.calls.map(([url]) => url)).toEqual([
      '/api/securities/XYZ/stop-loss?confluence=stop&position=mine',
    ]);
  });

  it('renders the three stops, the ladder, context and flags', async () => {
    mockApi([['/stop-loss', response()]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);

    const technical = await screen.findByTestId('stop-tile-technical');
    expect(technical.textContent).toContain('$173.54');
    expect(technical.textContent).toContain('-3.6%');
    expect(technical.textContent).toContain('sma 20 $176.00');
    expect(technical.textContent).not.toContain('noise floor');

    const trailing = screen.getByTestId('stop-tile-trailing');
    expect(trailing.textContent).toContain('$168.30');
    expect(trailing.textContent).toContain('if placed today');
    expect(trailing.textContent).toContain('6.5% trail');

    const confluence = screen.getByTestId('stop-tile-confluence');
    expect(confluence.textContent).toContain('proposed');
    expect(confluence.textContent).toContain('$168.65');
    expect(confluence.textContent).toContain('under $170.00–$171.50');
    expect(confluence.textContent).toContain('3 methods agree');

    const fiveDay = screen.getByTestId('replay-max_5day');
    expect(fiveDay.textContent).toContain('Worst 5-day drop');
    expect(fiveDay.textContent).toContain('-11.4% → $159.48');
    expect(within(fiveDay).getAllByTitle('This drop reaches the stop')).toHaveLength(3);
    expect(within(screen.getByTestId('replay-recent_1day'))
      .getAllByTitle('This drop stays above the stop')).toHaveLength(3);

    expect(screen.getByTestId('stop-context').textContent).toContain('2.3% float');
    expect(screen.getByTestId('stop-flags').textContent).toContain('bullish macd');
    expect(screen.getByText('XYZ at $180.00 — uptrend.')).toBeInTheDocument();

    // Not held: no chip and no P&L block.
    expect(screen.queryByTestId('stop-held-chip')).toBeNull();
    expect(screen.queryByTestId('stop-position')).toBeNull();
  });

  it('warns when the technical stop sits inside the noise floor', async () => {
    const base = response();
    mockApi([['/stop-loss', response({
      stops: { ...base.stops, technical_stop_inside_noise_floor: true },
    })]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    expect((await screen.findByTestId('stop-tile-technical')).textContent)
      .toContain('Inside the noise floor: closer than the worst 1-day drop (-1.5%)');
  });

  it('shows the reason when no confluence stop qualifies, and drops its hit chip', async () => {
    const base = response();
    mockApi([['/stop-loss', response({
      stops: {
        ...base.stops, confluence_stop: null,
        confluence_stop_reason: 'No support zone where 2 or more methods agree.',
      },
      drawdown_replay: base.drawdown_replay.map((r) => ({ ...r, hits_confluence: null })),
    })]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    const tile = await screen.findByTestId('stop-tile-confluence');
    expect(tile.textContent).toContain('—');
    expect(tile.textContent).toContain('No support zone where 2 or more methods agree.');
    expect(within(screen.getByTestId('replay-max_5day')).getAllByTitle(/This drop/)).toHaveLength(2);
  });

  it('names the skipped zone when the stop stepped past the noise floor', async () => {
    const base = response();
    mockApi([['/stop-loss', response({
      stops: {
        ...base.stops,
        confluence_stop: { ...CONFLUENCE_STOP, stepped_past_noise: true },
        confluence_stop_reason: 'Stepped past $178.00: its stop was inside the noise floor.',
      },
    })]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    expect((await screen.findByTestId('stop-tile-confluence')).textContent)
      .toContain('Stepped past $178.00');
  });

  it('shows the Held chip and P&L at each stop for a held symbol', async () => {
    mockApi([['/stop-loss', response({ position: HELD, position_source: 'lots' })]]);
    renderWithProviders(<StopLossCard ticker="XYZ" />);
    expect(await screen.findByTestId('stop-held-chip')).toBeInTheDocument();
    const position = screen.getByTestId('stop-position');
    expect(position.textContent).toContain('10 sh @ $150.00');
    expect(position.textContent).toContain('+$300.00 (+20.0%)');
    expect(position.textContent).toContain('At technical stop+$235.40');
    expect(position.textContent).toContain('At trailing stop+$183.00');
    expect(position.textContent).toContain('At confluence stop+$186.50');
  });

  it('seeds the support-confluence query from a confluence=full response', async () => {
    const embedded = { symbol: 'XYZ', price: 180 } as SupportConfluenceResponse;
    const api = mockApi([['/stop-loss', response({ confluence: embedded })]]);
    // The default gcTime: the test client's gcTime of 0 would drop the seeded,
    // unobserved entry before it could be read (on the page the confluence card observes it).
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithProviders(<StopLossCard ticker="XYZ" confluence="full" />, { queryClient: client });
    await screen.findByTestId('stop-tile-technical');
    expect(api.calls[0][0]).toContain('confluence=full&position=mine');
    expect(client.getQueryData(['support-confluence', 'XYZ'])).toEqual(embedded);
  });
});
