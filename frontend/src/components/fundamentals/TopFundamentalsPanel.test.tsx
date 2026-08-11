import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import TopFundamentalsPanel from './TopFundamentalsPanel';
import { fundamentalRanking, mockApi, renderWithProviders, topFundamentals } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOP = '/api/securities/fundamentals/top';

describe('TopFundamentalsPanel', () => {
  it('shows a spinner while the request is in flight', () => {
    mockApi([[TOP, topFundamentals()]]);
    renderWithProviders(<TopFundamentalsPanel />);
    expect(screen.getByText(/Ranking the tracked universe/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([[TOP, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<TopFundamentalsPanel />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load the fundamental rankings/i)).toBeInTheDocument(),
    );
  });

  it('renders the ranked table with the server-supplied rank, score and label', async () => {
    mockApi([
      [
        TOP,
        topFundamentals([
          fundamentalRanking('INTC', { rank: 1, composite_score: 72, coverage: 0.9 }),
          fundamentalRanking('WMT', {
            rank: 2,
            composite_score: 61,
            fundamental_label: 'mixed',
            coverage: 0.75,
          }),
        ]),
      ],
    ]);
    renderWithProviders(<TopFundamentalsPanel />);
    await waitFor(() => expect(screen.getByTestId('top-fundamentals-panel')).toBeInTheDocument());

    expect(screen.getByText('INTC')).toBeInTheDocument();
    expect(screen.getByText('72')).toBeInTheDocument();
    expect(screen.getByText('strong')).toBeInTheDocument();
    expect(screen.getByText('WMT')).toBeInTheDocument();
    expect(screen.getByText('61')).toBeInTheDocument();
    // coverage arrives as a fraction — formatPercent scales it, formatPercentRaw
    // would print "0.9%".
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('75%')).toBeInTheDocument();
  });

  it('reports the scored count against the tracked count, so a short list reads as a backlog', async () => {
    mockApi([
      [
        TOP,
        topFundamentals([fundamentalRanking('INTC')], {
          eligible_count: 1,
          total_in_cache: 40,
        }),
      ],
    ]);
    renderWithProviders(<TopFundamentalsPanel />);
    await waitFor(() => expect(screen.getByText('1 scored / 40 tracked')).toBeInTheDocument());
  });

  it('explains an empty ranking instead of rendering a bare table', async () => {
    mockApi([[TOP, topFundamentals([], { eligible_count: 0, total_in_cache: 12 })]]);
    renderWithProviders(<TopFundamentalsPanel />);
    await waitFor(() =>
      expect(screen.getByText(/nightly warmer converges/i)).toBeInTheDocument(),
    );
    // The header still reports the backlog — that is the useful part when the
    // list is empty.
    expect(screen.getByText('0 scored / 12 tracked')).toBeInTheDocument();
  });

  it('dashes out a missing score rather than printing a zero', async () => {
    mockApi([
      [
        TOP,
        topFundamentals([
          fundamentalRanking('NEW', {
            composite_score: null,
            fundamental_label: null,
            coverage: null,
          }),
        ]),
      ],
    ]);
    renderWithProviders(<TopFundamentalsPanel />);
    await waitFor(() => expect(screen.getByText('NEW')).toBeInTheDocument());

    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('asks for ten rows and drops the coverage column in the rail variant', async () => {
    const api = mockApi([[TOP, topFundamentals([fundamentalRanking('INTC')])]]);
    renderWithProviders(<TopFundamentalsPanel variant="rail" />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    expect(api.calls[0][0]).toContain('n=10');
    // scope is what makes the numbers meaningful — it must be on the wire.
    expect(api.calls[0][0]).toContain('scope=tracked');
    expect(screen.queryByText('Coverage')).not.toBeInTheDocument();
  });

  it('asks for twenty-five rows on the page variant', async () => {
    const api = mockApi([[TOP, topFundamentals([fundamentalRanking('INTC')])]]);
    renderWithProviders(<TopFundamentalsPanel />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    expect(api.calls[0][0]).toContain('n=25');
    expect(screen.getByText('Coverage')).toBeInTheDocument();
  });
});
