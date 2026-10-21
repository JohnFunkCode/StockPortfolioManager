/** SentimentDashboard (moved from the retired Securities page, #224): it only renders. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import SentimentDashboard from './SentimentDashboard';
import { renderWithProviders } from '../../testUtils';
import type { BulkSentimentResponse, SentimentSummaryItem } from '../../api/securitiesTypes';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const item = (symbol: string, overall: SentimentSummaryItem['overall_sentiment']): SentimentSummaryItem => ({
  symbol, name: `${symbol} Corp`, source: 'watchlist', tags: [], captured_at: '2026-10-20T21:00:00Z',
  overall_sentiment: overall, positive_count: 3, negative_count: 1, neutral_count: 2,
  scored_count: 6, article_count: 6,
});

const DATA: BulkSentimentResponse = {
  items: [item('INTC', 'positive'), item('WMT', 'negative'), item('KO', null)],
  count: 3,
};

const draw = (props: Partial<Parameters<typeof SentimentDashboard>[0]> = {}) => {
  const onRefresh = vi.fn();
  renderWithProviders(
    <SentimentDashboard open data={DATA} isLoading={false} onRefresh={onRefresh} {...props} />,
  );
  return onRefresh;
};

afterEach(() => mockNavigate.mockReset());

describe('SentimentDashboard', () => {
  it('shows a loading state and disables Refresh', () => {
    draw({ data: undefined, isLoading: true });
    expect(screen.getByText('Loading sentiment data…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Refresh/ })).toBeDisabled();
  });

  it('explains how to get data when nothing is scored', () => {
    draw({ data: { items: [], count: 0 } });
    expect(screen.getByText(/No sentiment data yet/)).toBeInTheDocument();
  });

  it('counts each sentiment and lists every scored security', () => {
    draw();
    expect(screen.getByText('↓ 1 negative')).toBeInTheDocument();
    expect(screen.getByText('– 0 neutral')).toBeInTheDocument();
    expect(screen.getByText('↑ 1 positive')).toBeInTheDocument();
    expect(screen.getByText('3 scored securities')).toBeInTheDocument();
    expect(screen.getByText('INTC Corp')).toBeInTheDocument();
    // A null overall sentiment reads as a dash, not as "neutral".
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getAllByText('2026-10-20')).toHaveLength(3);
  });

  it('refreshes on demand and opens a security from its row', () => {
    const onRefresh = draw();
    fireEvent.click(screen.getByRole('button', { name: /Refresh/ }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('WMT'));
    expect(mockNavigate).toHaveBeenCalledWith('/securities/WMT');
  });
});
