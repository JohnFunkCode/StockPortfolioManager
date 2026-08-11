import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import FundamentalsPage from './FundamentalsPage';
import {
  fundamentalRanking,
  fundamentalsCacheStats,
  mockApi,
  renderWithProviders,
  scoreChange,
  scoreChanges,
  sectorBreakdown,
  sectorRanking,
  topFundamentals,
  upcomingEarning,
  upcomingEarnings,
} from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const BASE = '/api/securities/fundamentals';
const TOP = `${BASE}/top`;
const CHANGES = `${BASE}/score-changes`;
const SECTORS = `${BASE}/sector-breakdown`;
const EARNINGS = `${BASE}/upcoming-earnings`;
const STATS = `${BASE}/cache-stats`;

function allRoutes(overrides: Array<[string, object]> = []) {
  // Overrides first — mockApi matches in order, so an override wins.
  return mockApi([
    ...overrides,
    [TOP, topFundamentals([fundamentalRanking('INTC', { composite_score: 72 })])],
    [CHANGES, scoreChanges([scoreChange('AVGO', { delta: 12, score_now: 80 })])],
    // A symbol no other panel shows, so a query can name one panel's output.
    [SECTORS, sectorBreakdown({ Energy: [sectorRanking('XOM')] })],
    [EARNINGS, upcomingEarnings([upcomingEarning('WMT')])],
    [STATS, fundamentalsCacheStats()],
  ] as Array<[string, object]>);
}

describe('FundamentalsPage', () => {
  it('renders the heading and names the universe it is ranking', async () => {
    allRoutes();
    renderWithProviders(<FundamentalsPage />);
    expect(screen.getByText('Fundamentals')).toBeInTheDocument();
    expect(screen.getByText('tracked universe')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('top-fundamentals-panel')).toBeInTheDocument());
  });

  it('renders all five panels', async () => {
    allRoutes();
    renderWithProviders(<FundamentalsPage />);

    await waitFor(() => expect(screen.getByTestId('top-fundamentals-panel')).toBeInTheDocument());
    expect(screen.getByTestId('score-changes-panel')).toBeInTheDocument();
    expect(screen.getByTestId('sector-breakdown-panel')).toBeInTheDocument();
    expect(screen.getByTestId('upcoming-earnings-strip')).toBeInTheDocument();
    expect(screen.getByTestId('cache-freshness-banner')).toBeInTheDocument();
  });

  it('fetches every panel with scope=tracked', async () => {
    const api = allRoutes();
    renderWithProviders(<FundamentalsPage />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThanOrEqual(5));

    const scoped = api.calls.map(([url]) => url).filter((url) => !url.includes('cache-stats'));
    expect(scoped).toHaveLength(4);
    scoped.forEach((url) => expect(url).toContain('scope=tracked'));
    // The banner reports on the cache itself, so it deliberately takes no scope.
    expect(api.calls.some(([url]) => url.includes('cache-stats') && !url.includes('scope='))).toBe(
      true,
    );
    expect(api.unmatched).toEqual([]);
  });

  it('leaves the other panels standing when one query fails', async () => {
    allRoutes([[CHANGES, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<FundamentalsPage />);

    await waitFor(() =>
      expect(screen.getByText(/Couldn't load fundamental score changes/i)).toBeInTheDocument(),
    );
    expect(screen.getByTestId('top-fundamentals-panel')).toBeInTheDocument();
    expect(screen.getByTestId('sector-breakdown-panel')).toBeInTheDocument();
    expect(screen.getByTestId('upcoming-earnings-strip')).toBeInTheDocument();
    expect(screen.getByText('INTC')).toBeInTheDocument();
  });

  it('renders the page without a cache banner when the stats read fails', async () => {
    allRoutes([[STATS, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<FundamentalsPage />);

    await waitFor(() => expect(screen.getByTestId('top-fundamentals-panel')).toBeInTheDocument());
    expect(screen.queryByTestId('cache-freshness-banner')).not.toBeInTheDocument();
  });
});
