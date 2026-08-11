import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import { FundamentalsScoreChangesCard, FundamentalsTopCard } from './FundamentalsCards';
import {
  fundamentalRanking,
  mockApi,
  renderWithProviders,
  scoreChange,
  scoreChanges,
  topFundamentals,
} from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOP = '/api/securities/fundamentals/top';
const CHANGES = '/api/securities/fundamentals/score-changes';

describe('FundamentalsTopCard', () => {
  it('shows a spinner while the request is in flight', () => {
    mockApi([[TOP, topFundamentals()]]);
    renderWithProviders(<FundamentalsTopCard />);
    expect(screen.getByText(/Ranking the tracked universe/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([[TOP, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<FundamentalsTopCard />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load the fundamental rankings/i)).toBeInTheDocument(),
    );
  });

  it('renders the ranking in the rail shape — ten rows, no coverage column', async () => {
    const api = mockApi([
      [TOP, topFundamentals([fundamentalRanking('INTC', { composite_score: 72 })])],
    ]);
    renderWithProviders(<FundamentalsTopCard />);
    await waitFor(() => expect(screen.getByTestId('top-fundamentals-panel')).toBeInTheDocument());

    expect(screen.getByText('INTC')).toBeInTheDocument();
    expect(screen.getByText('72')).toBeInTheDocument();
    // The card is the panel's rail variant, not a copy of it.
    expect(api.calls[0][0]).toContain('n=10');
    expect(api.calls[0][0]).toContain('scope=tracked');
    expect(screen.queryByText('Coverage')).not.toBeInTheDocument();
  });
});

describe('FundamentalsScoreChangesCard', () => {
  it('shows a spinner while the request is in flight', () => {
    mockApi([[CHANGES, scoreChanges()]]);
    renderWithProviders(<FundamentalsScoreChangesCard />);
    expect(screen.getByText(/Comparing snapshots/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([[CHANGES, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<FundamentalsScoreChangesCard />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load fundamental score changes/i)).toBeInTheDocument(),
    );
  });

  it('renders the movers in the rail shape — no window column', async () => {
    const api = mockApi([
      [CHANGES, scoreChanges([scoreChange('AVGO', { score_then: 60, score_now: 72 })])],
    ]);
    renderWithProviders(<FundamentalsScoreChangesCard />);
    await waitFor(() => expect(screen.getByTestId('score-changes-panel')).toBeInTheDocument());

    expect(screen.getByText('AVGO')).toBeInTheDocument();
    expect(screen.getByText('60 → 72')).toBeInTheDocument();
    expect(api.calls[0][0]).toContain('scope=tracked');
    expect(screen.queryByText('Window')).not.toBeInTheDocument();
  });
});
