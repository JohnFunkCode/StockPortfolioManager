import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';

import OptionsFlowPanel from './OptionsFlowPanel';
import { FLOW, pending } from './signalsFixtures';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('OptionsFlowPanel', () => {
  it('shows a spinner while the flow signals load', () => {
    mockApi([['/signals/options-flow', pending]]);
    renderWithProviders(<OptionsFlowPanel ticker="INTC" />);
    expect(screen.getByText('Options Flow (Smart Money)')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows an error with Retry', async () => {
    mockApi([['/signals/options-flow', { __status: 500, error: 'boom' }]]);
    renderWithProviders(<OptionsFlowPanel ticker="INTC" />);
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('renders the DAOI block and the first eight unusual calls', async () => {
    mockApi([['/signals/options-flow', FLOW]]);
    renderWithProviders(<OptionsFlowPanel ticker="INTC" />);
    expect(await screen.findByText((-1000).toLocaleString())).toHaveStyle({ color: '#ef4444' });
    expect(screen.getByText('Sell on rally')).toBeInTheDocument();
    expect(screen.getByText('$105')).toBeInTheDocument();
    expect(screen.getByText('$100')).toBeInTheDocument();
    expect(screen.getByText('+1.5%')).toBeInTheDocument();
    expect(screen.getByText('Signal: moderate')).toBeInTheDocument();
    expect(screen.getByText('MM net long')).toBeInTheDocument();
    expect(screen.getByText('bullish sweep signal')).toBeInTheDocument();

    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows).toHaveLength(9); // header + 8 of 9 calls
    expect(within(rows[1]).getByText('2.50×')).toHaveStyle({ color: '#10b981' });
    expect(within(rows[1]).getByText('-1.5%')).toHaveStyle({ color: '#10b981' });
    expect(within(rows[2]).getByText('+3.2%')).toHaveStyle({ color: 'var(--qc-muted)' });
    expect(within(rows[1]).getByText('very high')).toBeInTheDocument();
    expect(screen.queryByText('2026-11-18')).toBeNull();
  });

  it('falls back when DAOI and unusual calls are missing, and warns on partial errors', async () => {
    mockApi([['/signals/options-flow', {
      ...FLOW, delta_adjusted_oi: null, unusual_calls: null, _errors: { unusual_calls: 'timeout' },
    }]]);
    renderWithProviders(<OptionsFlowPanel ticker="INTC" />);
    expect(await screen.findByText('Delta-adjusted OI not available (no options data).')).toBeInTheDocument();
    expect(screen.getByText('Unusual call data not available.')).toBeInTheDocument();
    expect(screen.getByText(/Some flow signals failed: unusual_calls/)).toBeInTheDocument();
  });

  it('shows the interpretation when there are no unusual calls', async () => {
    mockApi([['/signals/options-flow', { ...FLOW, unusual_calls: { ...FLOW.unusual_calls!, unusual_calls: [] } }]]);
    renderWithProviders(<OptionsFlowPanel ticker="INTC" />);
    expect(await screen.findByText('no unusual activity')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });
});
