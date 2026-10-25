import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';

import SignalsTab from './SignalsTab';
import { mockApi, renderWithProviders } from '../../testUtils';

const TECH = {
  ticker: 'INTC',
  stochastic: { k: 25, d: 30, signal: 'oversold' },
  vwap: { vwap: 101.5, position: 'above_vwap', reclaim_signal: true, reclaim_strength: 'strong', distance_pct: 1.2 },
  obv: { divergence: 'bullish', divergence_strength: 'moderate', obv_trend: 'rising', price_trend: 'rising' },
  volume_analysis: { bottom_signal: 'weak', last_volume_ratio: 1.4, climax_events: [] },
  candlestick_patterns: { bounce_signal: 'no pattern', patterns_found: [] },
  higher_lows: { higher_low_pattern: false, pattern_strength: 'none', swing_lows: [] },
  gap_analysis: { unfilled_count: 0, bounce_targets: [], all_gaps: [] },
  _errors: null,
};

const FLOW = {
  ticker: 'INTC',
  unusual_calls: { sweep_signal: 'none', unusual_calls: [], interpretation: 'no unusual activity' },
  delta_adjusted_oi: {
    signal: 'none', mm_hedge_bias: 'sell_on_rally', mm_note: 'MM net long',
    net_daoi_shares: -1000, delta_flip_strike: 100, dist_to_flip_pct: 1.5, gamma_wall_strike: 105,
  },
  _errors: null,
};

const RISK = {
  ticker: 'INTC',
  drawdown: {
    trailing_stop_pct: 8.5, max_1day_drawdown_pct: -5.0, max_5day_drawdown_pct: -9.0,
    max_intraday_drop_pct: -3.2, recent_max_1day_pct: -4.1, stop_width_note: 'note',
  },
  vwap: 101.2,
  vwap_position: 'above_vwap',
};

const NEWS = { symbol: 'INTC', article_count: 0, articles: [] };

const TREND = {
  symbol: 'INTC', days: 91, bucket: 'week', weeks: 1, history_start: '2026-09-30',
  weeks_with_articles: 1, total_articles: 12, thin_threshold: 10,
  buckets: [{
    week_start: '2026-10-19', week_end: '2026-10-25', article_count: 12, positive_count: 2,
    negative_count: 6, neutral_count: 4, net_score: -0.333, label: 'negative', thin: false,
    in_progress: true, before_history: false, flip: null,
  }],
};

function armAll(overrides: Record<string, unknown> = {}) {
  return mockApi([
    ['/signals/technical', overrides.tech ?? TECH],
    ['/signals/options-flow', overrides.flow ?? FLOW],
    ['/signals/risk', overrides.risk ?? RISK],
    // Before '/news', which would otherwise match the trend URL too.
    ['/news/trend', overrides.trend ?? TREND],
    ['/news', overrides.news ?? NEWS],
  ]);
}

afterEach(() => vi.unstubAllGlobals());

describe('SignalsTab', () => {
  it('renders the section headers once signals load', async () => {
    armAll();
    renderWithProviders(<SignalsTab ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText('Momentum & Volume')).toBeInTheDocument(),
    );
    expect(screen.getByText('Price Structure')).toBeInTheDocument();
  });

  it('shows signal badges from the technical payload', async () => {
    armAll();
    renderWithProviders(<SignalsTab ticker="INTC" />);
    // 'oversold' appears both in the interpretation summary and the badge.
    await waitFor(() =>
      expect(screen.getAllByText(/oversold/i).length).toBeGreaterThan(0),
    );
    // The interpretation summary synthesizes the bullish read from the payload.
    expect(screen.getByText(/broadly bullish/i)).toBeInTheDocument();
  });

  it('synthesizes a bearish read and renders the news sentiment summary', async () => {
    armAll({
      tech: {
        ...TECH,
        stochastic: { k: 85, d: 82, signal: 'overbought' },
        vwap: { vwap: 99, position: 'below_vwap', reclaim_signal: false, reclaim_strength: 'none', distance_pct: -2.1 },
        obv: { divergence: 'bearish', divergence_strength: 'strong', obv_trend: 'falling', price_trend: 'falling' },
      },
      news: {
        symbol: 'INTC',
        article_count: 3,
        articles: [{ title: 'Guidance cut', sentiment: 'negative', sentiment_score: 0.9, publisher: 'Wire', published: '2026-07-20', url: 'u' }],
        sentiment_summary: { overall: 'negative', positive_count: 0, negative_count: 3, neutral_count: 0, scored_count: 3 },
      },
    });
    renderWithProviders(<SignalsTab ticker="INTC" />);
    await waitFor(() =>
      expect(screen.getByText(/broadly bearish/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/News sentiment across 3/i)).toBeInTheDocument();
  });

  it('puts the weekly trend between the Overall row and the articles (#411)', async () => {
    armAll({
      news: {
        symbol: 'INTC',
        article_count: 1,
        articles: [{ title: 'Guidance cut', sentiment: 'negative', sentiment_score: 0.9, publisher: 'Wire', published: '2026-07-20', url: 'u' }],
        sentiment_summary: { overall: 'negative', positive_count: 0, negative_count: 1, neutral_count: 0, scored_count: 1 },
      },
    });
    renderWithProviders(<SignalsTab ticker="INTC" />);
    const trend = await screen.findByTestId('sentiment-trend-chart');
    const overall = screen.getByText('Overall:');
    const article = screen.getByText('Guidance cut');
    // DOCUMENT_POSITION_FOLLOWING: the second node comes after the first.
    expect(overall.compareDocumentPosition(trend) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(trend.compareDocumentPosition(article) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the Overall row with its articles: none without them (#439 review)', async () => {
    armAll({ news: { ...NEWS, sentiment_summary: {
      overall: 'negative', positive_count: 0, negative_count: 2, neutral_count: 1, scored_count: 3,
    } } });
    renderWithProviders(<SignalsTab ticker="INTC" />);
    expect(await screen.findByText('No recent news available.')).toBeInTheDocument();
    expect(screen.queryByText('Overall:')).toBeNull();
  });

  it('still shows the trend when there is no current news', async () => {
    armAll();
    renderWithProviders(<SignalsTab ticker="INTC" />);
    expect(await screen.findByText('No recent news available.')).toBeInTheDocument();
    expect(await screen.findByTestId('sentiment-trend-chart')).toBeInTheDocument();
  });

  it('shows the trend while the live article list is still loading', async () => {
    // Prod's /news scores headlines live and took 64 s for WDC; /news/trend
    // reads stored history, so it must not wait behind it.
    armAll({ news: () => new Promise(() => {}) });
    renderWithProviders(<SignalsTab ticker="INTC" />);
    expect(await screen.findByTestId('sentiment-trend-chart')).toBeInTheDocument();
    const panel = screen.getByTestId('sentiment-trend').parentElement!.parentElement!;
    expect(within(panel).getByRole('progressbar')).toBeInTheDocument();
    expect(screen.queryByText('Overall:')).toBeNull();
  });

  it('no longer renders the Risk panel, but the summary still uses the risk signals', async () => {
    armAll();
    renderWithProviders(<SignalsTab ticker="INTC" />);
    // Stops moved to StopLossCard on the Technical Analysis tab (#399).
    await waitFor(() =>
      expect(screen.getByText(/minimum trailing stop of 8\.5%/)).toBeInTheDocument(),
    );
    expect(screen.queryByText('Risk & Stop-Loss Calibration')).toBeNull();
  });

  it('composes the five sections in their original order (#441 review)', async () => {
    armAll();
    renderWithProviders(<SignalsTab ticker="INTC" />);
    const titles = ['Interpretation', 'Momentum & Volume', 'Price Structure',
      'Options Flow (Smart Money)', 'Recent News & Sentiment'];
    const nodes = await Promise.all(titles.map((t) => screen.findByText(t)));
    for (let i = 1; i < nodes.length; i++) {
      expect(nodes[i - 1].compareDocumentPosition(nodes[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it('degrades a section when its endpoint errors while others render', async () => {
    armAll({ risk: { __status: 500, error: 'risk unavailable' } });
    renderWithProviders(<SignalsTab ticker="INTC" />);
    // The momentum section (technical) still renders from its own query.
    await waitFor(() =>
      expect(screen.getByText('Momentum & Volume')).toBeInTheDocument(),
    );
  });
});
