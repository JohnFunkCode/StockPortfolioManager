import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';

import SentimentTrendChart from './SentimentTrendChart';
import { mockApi, renderWithProviders } from '../../testUtils';
import type { SentimentTrendResponse, SentimentTrendWeek } from '../../api/securitiesTypes';

afterEach(() => vi.unstubAllGlobals());

function week(start: string, over: Partial<SentimentTrendWeek> = {}): SentimentTrendWeek {
  return {
    week_start: start, week_end: start, article_count: 0, positive_count: 0,
    negative_count: 0, neutral_count: 0, net_score: null, label: null, thin: false,
    in_progress: false, before_history: false, flip: null, ...over,
  };
}

// WDC on prod, 2026-10-24, trimmed to five weeks: one before history, then
// the real weekly numbers including the uncollected Oct 5 week.
function wdc(): SentimentTrendResponse {
  return {
    symbol: 'WDC', days: 35, bucket: 'week', weeks: 5, history_start: '2026-09-30',
    weeks_with_articles: 3, total_articles: 98, thin_threshold: 10,
    buckets: [
      week('2026-09-21', { before_history: true }),
      week('2026-09-28', {
        article_count: 15, positive_count: 3, negative_count: 6, neutral_count: 6,
        net_score: -0.2, label: 'neutral',
      }),
      week('2026-10-05'),
      week('2026-10-12', {
        article_count: 34, positive_count: 1, negative_count: 14, neutral_count: 19,
        net_score: -0.382, label: 'neutral',
      }),
      week('2026-10-19', {
        article_count: 49, positive_count: 13, negative_count: 16, neutral_count: 20,
        net_score: -0.061, label: 'neutral', in_progress: true,
      }),
    ],
  };
}

describe('SentimentTrendChart', () => {
  it('shows a spinner while loading, then the chart', async () => {
    let resolve!: (body: object) => void;
    const pending = new Promise<object>((r) => { resolve = r; });
    mockApi([['/news/trend', () => pending]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    expect(await screen.findByRole('progressbar')).toBeInTheDocument();

    await act(async () => { resolve(wdc()); });
    expect(await screen.findByTestId('sentiment-trend-chart')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('shows an error whose Retry recovers', async () => {
    let calls = 0;
    mockApi([['/news/trend', () => {
      calls += 1;
      return calls === 1 ? { __status: 500, error: 'boom' } : wdc();
    }]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    expect(await screen.findByText(/Couldn't load the sentiment trend/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByTestId('sentiment-trend-chart')).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it('asks for 13 weekly buckets by default', async () => {
    const api = mockApi([['/news/trend', wdc()]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');
    expect(api.calls[0][0]).toContain('/api/securities/WDC/news/trend?bucket=week&days=91');
  });

  it('states coverage and keeps the three empty states apart', async () => {
    mockApi([['/news/trend', wdc()]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    expect(screen.getByText(/3 of 5 weeks with articles · 98 articles · history since Sep 30/))
      .toBeInTheDocument();

    const before = screen.getByTestId('trend-week-2026-09-21');
    expect(within(before).queryByTestId('trend-bar')).toBeNull();
    expect(within(before).queryByTestId('trend-empty')).toBeNull();

    const gap = screen.getByTestId('trend-week-2026-10-05');
    expect(within(gap).getByTestId('trend-empty')).toBeInTheDocument();
    expect(within(gap).getByText('0 articles')).toBeInTheDocument();

    const measured = screen.getByTestId('trend-week-2026-10-12');
    expect(within(measured).getByTestId('trend-bar')).toBeInTheDocument();
    expect(within(measured).getByTestId('trend-caption')).toHaveTextContent('−0.38 · 34');
  });

  it('splits each caption so a narrow column can drop the count, then the net', async () => {
    mockApi([['/news/trend', wdc()]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    // The container queries hide .trend-detail, then .trend-net; jsdom can't
    // evaluate them, so pin the structure they select on.
    const measured = within(screen.getByTestId('trend-week-2026-10-12')).getByTestId('trend-caption');
    expect(measured.querySelector('.trend-net')).toHaveTextContent('−0.38');
    expect(measured.querySelector('.trend-detail')).toHaveTextContent('· 34');
    expect(measured).toHaveStyle({ fontVariantNumeric: 'tabular-nums' });

    // An empty week's "0 articles" is detail only: the dashed outline says it
    // where there is no room, and a bare "0" would read as a net score.
    const gap = within(screen.getByTestId('trend-week-2026-10-05')).getByTestId('trend-caption');
    expect(gap.querySelector('.trend-net')).toBeNull();
    expect(gap.querySelector('.trend-detail')).toHaveTextContent('0 articles');

    const before = within(screen.getByTestId('trend-week-2026-09-21')).getByTestId('trend-caption');
    expect(before).toBeEmptyDOMElement();
  });

  it('caps the caption count at four characters and keeps the exact count in the tooltip text', async () => {
    const data = wdc();
    data.buckets[1] = { ...data.buckets[1], article_count: 999 };
    data.buckets[3] = { ...data.buckets[3], article_count: 12_345 };
    data.buckets[4] = { ...data.buckets[4], article_count: 999_999 };
    mockApi([['/news/trend', data]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    const chart = await screen.findByTestId('sentiment-trend-chart');

    const detail = (start: string) =>
      within(screen.getByTestId(`trend-week-${start}`)).getByTestId('trend-caption')
        .querySelector('.trend-detail');
    expect(detail('2026-09-28')).toHaveTextContent('· 999');
    expect(detail('2026-10-12')).toHaveTextContent('· 12K');
    expect(detail('2026-10-19')).toHaveTextContent('· 1M');
    expect(chart.getAttribute('aria-label')).toContain('12345 articles');
  });

  it('marks the in-progress week and draws thin weeks paler', async () => {
    const data = wdc();
    data.buckets[1] = { ...data.buckets[1], article_count: 6, thin: true };
    mockApi([['/news/trend', data]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    const current = screen.getByTestId('trend-week-2026-10-19');
    expect(within(current).getByTestId('trend-in-progress')).toBeInTheDocument();
    expect(within(screen.getByTestId('trend-week-2026-10-12'))
      .queryByTestId('trend-in-progress')).toBeNull();

    const thinBar = within(screen.getByTestId('trend-week-2026-09-28')).getByTestId('trend-bar');
    const fullBar = within(screen.getByTestId('trend-week-2026-10-12')).getByTestId('trend-bar');
    expect(getComputedStyle(thinBar).opacity).toBe('0.45');
    expect(getComputedStyle(fullBar).opacity).toBe('1');
  });

  it('marks a flip with its direction', async () => {
    const data = wdc();
    data.buckets[3] = { ...data.buckets[3], label: 'negative', flip: 'deteriorating' };
    mockApi([['/news/trend', data]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    const flips = screen.getAllByTestId('trend-flip');
    expect(flips).toHaveLength(1);
    expect(flips[0]).toHaveAccessibleName('sentiment flip: deteriorating');
    expect(within(screen.getByTestId('trend-week-2026-10-12')).getByTestId('trend-flip'))
      .toBe(flips[0]);
  });

  it('marks an improving flip with an up arrow', async () => {
    const data = wdc();
    data.buckets[4] = { ...data.buckets[4], label: 'positive', flip: 'improving' };
    mockApi([['/news/trend', data]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    const flip = within(screen.getByTestId('trend-week-2026-10-19')).getByTestId('trend-flip');
    expect(flip).toHaveAccessibleName('sentiment flip: improving');
    expect(flip).toHaveTextContent('▲');
  });

  it('draws positive bars up from the zero line, negative bars down, and zero as a hairline', async () => {
    const data = wdc();
    data.buckets[1] = { ...data.buckets[1], net_score: 0.5, positive_count: 9, negative_count: 1 };
    data.buckets[4] = { ...data.buckets[4], net_score: 0 };
    mockApi([['/news/trend', data]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    const bar = (start: string) =>
      getComputedStyle(within(screen.getByTestId(`trend-week-${start}`)).getByTestId('trend-bar'));

    const up = bar('2026-09-28');
    expect(up.bottom).toBe('60px');
    expect(up.height).toBe('30px');
    expect(up.top).not.toBe('60px');

    const down = bar('2026-10-12');
    expect(down.top).toBe('60px');
    expect(parseFloat(down.height)).toBeCloseTo(0.382 * 60, 1);
    expect(down.bottom).not.toBe('60px');

    const flat = bar('2026-10-19');
    expect(flat.bottom).toBe('60px');
    expect(flat.height).toBe('2px');
  });

  it('shows a date it cannot parse as given rather than "undefined"', async () => {
    mockApi([['/news/trend', { ...wdc(), history_start: 'unknown' }]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');
    expect(screen.getByText(/history since unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it('describes the series in words for screen readers', async () => {
    mockApi([['/news/trend', wdc()]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    const chart = await screen.findByRole('img', { name: /Weekly net news sentiment for WDC/ });
    const name = chart.getAttribute('aria-label') ?? '';
    expect(name).toContain('Week of Oct 12: 34 articles');
    expect(name).toContain('net −0.38');
    expect(name).toContain('2 weeks without articles');
  });

  it('labels the shaded run before history starts', async () => {
    const data = wdc();
    data.buckets[1] = week('2026-09-28', { before_history: true });
    mockApi([['/news/trend', data]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    expect(await screen.findByText('No news history before Sep 30')).toBeInTheDocument();
  });

  it('refetches when the window changes', async () => {
    const api = mockApi([['/news/trend', (url: string) => {
      const days = Number(new URL(url, 'http://x').searchParams.get('days'));
      return { ...wdc(), days, weeks: days / 7 };
    }]]);
    renderWithProviders(<SentimentTrendChart ticker="WDC" />);
    await screen.findByTestId('sentiment-trend-chart');

    fireEvent.click(screen.getByRole('button', { name: '4w' }));
    await waitFor(() =>
      expect(api.calls.some(([u]) => String(u).includes('days=28'))).toBe(true));
    expect(screen.getByRole('button', { name: '4w' })).toHaveAttribute('aria-pressed', 'true');
  });
});
