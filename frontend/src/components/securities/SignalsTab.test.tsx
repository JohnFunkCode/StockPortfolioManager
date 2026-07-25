import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

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

function armAll(overrides: Record<string, unknown> = {}) {
  return mockApi([
    ['/signals/technical', overrides.tech ?? TECH],
    ['/signals/options-flow', overrides.flow ?? FLOW],
    ['/signals/risk', overrides.risk ?? RISK],
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

  it('degrades a section when its endpoint errors while others render', async () => {
    armAll({ risk: { __status: 500, error: 'risk unavailable' } });
    renderWithProviders(<SignalsTab ticker="INTC" />);
    // The momentum section (technical) still renders from its own query.
    await waitFor(() =>
      expect(screen.getByText('Momentum & Volume')).toBeInTheDocument(),
    );
  });
});
