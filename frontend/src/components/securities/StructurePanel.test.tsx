import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';

import StructurePanel from './StructurePanel';
import { TECH, pending } from './signalsFixtures';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('StructurePanel', () => {
  it('shows a spinner while the technical signals load', () => {
    mockApi([['/signals/technical', pending]]);
    renderWithProviders(<StructurePanel ticker="INTC" />);
    expect(screen.getByText('Price Structure')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows an error with Retry', async () => {
    mockApi([['/signals/technical', { __status: 500, error: 'boom' }]]);
    renderWithProviders(<StructurePanel ticker="INTC" />);
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('renders higher lows, gap targets and the first five candlestick patterns', async () => {
    mockApi([['/signals/technical', TECH]]);
    renderWithProviders(<StructurePanel ticker="INTC" />);
    expect(await screen.findByText('strong — 3 consecutive')).toBeInTheDocument();
    expect(screen.getByText('Higher lows note')).toBeInTheDocument();
    expect(screen.getByText(/↑ \$95\.00–\$97\.00\s+\(-2\.0%\)/)).toBeInTheDocument();
    expect(screen.getByText(/↓ \$110\.00–\$112\.50\s+\(\+8\.4%\)/)).toBeInTheDocument();
    expect(screen.getByText('Gap note')).toBeInTheDocument();
    expect(screen.getByText(/Candlestick Patterns \(last 10 bars\) — bullish bounce/)).toBeInTheDocument();

    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows).toHaveLength(6); // header + 5 of 6 patterns
    expect(within(rows[1]).getByText('pattern_0')).toBeInTheDocument();
    expect(within(rows[1]).getByText('note 0a; note 0b')).toBeInTheDocument();
    expect(within(rows[2]).getByText('bearish')).toHaveStyle({ color: 'var(--qc-ink-red)' });
    expect(screen.queryByText('pattern_5')).toBeNull();
  });

  it('says "No pattern", dashes missing gaps and hides an empty candlestick table', async () => {
    mockApi([['/signals/technical', {
      ...TECH,
      higher_lows: { ...TECH.higher_lows, higher_low_pattern: false },
      gap_analysis: null,
      candlestick_patterns: { ...TECH.candlestick_patterns, pattern_count: 0 },
    }]]);
    renderWithProviders(<StructurePanel ticker="INTC" />);
    expect(await screen.findByText('No pattern')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });
});
