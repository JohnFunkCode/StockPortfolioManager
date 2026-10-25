import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import MomentumPanel from './MomentumPanel';
import { TECH, pending } from './signalsFixtures';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('MomentumPanel', () => {
  it('shows a spinner while the technical signals load', () => {
    mockApi([['/signals/technical', pending]]);
    renderWithProviders(<MomentumPanel ticker="INTC" />);
    expect(screen.getByText('Momentum & Volume')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows an error with Retry, and Retry refetches', async () => {
    const api = mockApi([['/signals/technical', { __status: 500, error: 'boom' }]]);
    renderWithProviders(<MomentumPanel ticker="INTC" />);
    fireEvent.click(await screen.findByRole('button', { name: /retry/i }));
    expect(api.calls.filter(([u]) => u.includes('/signals/technical')).length).toBeGreaterThan(1);
  });

  it('renders stochastic, VWAP, OBV and volume values', async () => {
    mockApi([['/signals/technical', TECH]]);
    renderWithProviders(<MomentumPanel ticker="INTC" />);
    expect(await screen.findByText('18.3')).toHaveStyle({ color: '#10b981' }); // %K oversold → green
    expect(screen.getByText('oversold')).toBeInTheDocument();
    expect(screen.getByText('$101.50')).toBeInTheDocument();
    expect(screen.getByText('Above')).toBeInTheDocument();
    expect(screen.getByText('+1.2%')).toBeInTheDocument();
    expect(screen.getByText('Reclaim: strong')).toBeInTheDocument();
    expect(screen.getByText('bullish div (moderate)')).toBeInTheDocument();
    expect(screen.getByText('2.50×')).toHaveStyle({ color: '#ef4444' });
    expect(screen.getByText('possible bottom')).toBeInTheDocument();
    // Only the first two climax events.
    expect(screen.getByText('2026-10-20 down vol 3.1×')).toBeInTheDocument();
    expect(screen.getByText('2026-10-21 up vol 2.2×')).toBeInTheDocument();
    expect(screen.queryByText('2026-10-22 up vol 2×')).toBeNull();
    expect(screen.getByText('VWAP note here')).toBeInTheDocument();
    expect(screen.getByText('OBV note here')).toBeInTheDocument();
  });

  it('dashes a missing block and warns about partial failures', async () => {
    mockApi([['/signals/technical', { ...TECH, stochastic: null, obv: null, _errors: { obv: 'x', stochastic: 'y' } }]]);
    renderWithProviders(<MomentumPanel ticker="INTC" />);
    expect(await screen.findByText(/Some signals failed to load: obv, stochastic/)).toBeInTheDocument();
    expect(screen.getAllByText('—')).toHaveLength(2);
    expect(screen.queryByText('OBV note here')).toBeNull();
  });

  it('dashes every block when the payload has none of them', async () => {
    mockApi([['/signals/technical', { ...TECH, stochastic: null, obv: null, vwap: null, volume_analysis: null }]]);
    renderWithProviders(<MomentumPanel ticker="INTC" />);
    expect(await screen.findAllByText('—')).toHaveLength(4);
    expect(screen.queryByText('VWAP note here')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
