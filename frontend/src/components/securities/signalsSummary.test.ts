import { describe, expect, it } from 'vitest';

import { buildSignalsSummary } from './signalsSummary';
import { FLOW, RISK, TECH } from './signalsFixtures';
import type { NewsResponse, TechnicalSignalsResponse } from '../../api/securitiesTypes';

const EMPTY_TECH: TechnicalSignalsResponse = {
  ticker: 'X', stochastic: null, vwap: null, obv: null, volume_analysis: null,
  candlestick_patterns: null, higher_lows: null, gap_analysis: null,
};
const tech = (over: Partial<TechnicalSignalsResponse>) => ({ ...EMPTY_TECH, ...over });
const news = (overall: 'positive' | 'negative' | 'neutral', scored = 3): NewsResponse => ({
  symbol: 'X', article_count: scored, articles: [],
  sentiment_summary: { overall, positive_count: 1, negative_count: 1, neutral_count: 1, scored_count: scored },
});

describe('buildSignalsSummary', () => {
  it('is empty with nothing loaded', () => {
    expect(buildSignalsSummary(undefined, undefined, undefined, undefined)).toEqual([]);
  });

  it('writes every family in order from a full payload', () => {
    const lines = buildSignalsSummary(TECH, FLOW, RISK, news('positive', 1));
    expect(lines).toEqual([
      'Technical picture is broadly bullish (5 bullish vs 0 bearish signals): stochastic oversold, '
        + 'price above VWAP, VWAP reclaim (strong), bullish OBV divergence (moderate), strong higher-low structure (3 consecutive).',
      'Volume is running 2.5× the average — unusually high activity that often precedes or confirms a directional move.',
      'A down-volume climax event (3.1× avg) was recorded on 2026-10-20 — these extremes often mark short-term exhaustion points.',
      '2 unfilled gaps remain as potential reversion targets: $95.00–$97.00 (-2.0%), $110.00–$112.50 (+8.4%).',
      `Delta-adjusted OI shows ${(-1000).toLocaleString()} net shares of market-maker hedge exposure — MMs are positioned to `
        + 'sell on rallies, with a gamma wall at $105 and a delta flip point at $100.',
      `Unusual call activity detected (9 sweeps, bullish signal): the largest is the $200 strike expiring 2026-11-10 with `
        + `${(12000).toLocaleString()} contracts at 2.5× vol/OI — very high conviction.`,
      'Historical drawdown analysis suggests a minimum trailing stop of 8.5% to avoid noise-driven exits (worst single-day drop: -5.0%).',
      'News sentiment across 1 recent article is broadly positive (1 positive, 1 negative, 1 neutral) — positive news flow supports bullish momentum.',
    ]);
  });

  it('reads bearish and mixed technical pictures', () => {
    const bear = tech({
      stochastic: { k: 85, d: 80, signal: 'overbought', last_close: 1 },
      vwap: { ...TECH.vwap!, position: 'below_vwap', reclaim_signal: false },
      obv: { ...TECH.obv!, divergence: 'bearish', obv_trend: 'falling', price_trend: 'falling' },
    });
    expect(buildSignalsSummary(bear, undefined, undefined, undefined)[0]).toBe(
      'Technical picture is broadly bearish (4 bearish vs 0 bullish signals): stochastic overbought, '
      + 'price below VWAP, bearish OBV divergence (moderate), OBV and price trending down.');

    const mixed = tech({ vwap: { ...TECH.vwap!, position: 'below_vwap', reclaim_signal: true } });
    expect(buildSignalsSummary(mixed, undefined, undefined, undefined)[0]).toBe(
      'Technical signals are mixed — bullish: VWAP reclaim (strong); bearish: price below VWAP.');

    const onlyBear = tech({ vwap: { ...TECH.vwap!, position: 'below_vwap', reclaim_signal: false } });
    expect(buildSignalsSummary(onlyBear, undefined, undefined, undefined)[0]).toBe(
      'Technical signals are mixed — no clear bullish signals; bearish: price below VWAP.');
  });

  it('counts candlestick bias and pluralizes one pattern', () => {
    const one = tech({ candlestick_patterns: { ...TECH.candlestick_patterns!, patterns_found: [
      TECH.candlestick_patterns!.patterns_found[1]] } });
    expect(buildSignalsSummary(one, undefined, undefined, undefined)[0]).toContain('bearish: 1 bearish candlestick pattern.');
  });

  it('flags thin volume and stays quiet at ordinary volume', () => {
    const vol = (r: number) => tech({ volume_analysis: { ...TECH.volume_analysis!, last_volume_ratio: r, climax_events: [] } });
    expect(buildSignalsSummary(vol(0.3), undefined, undefined, undefined)).toEqual([
      'Volume is thin at 0.3× average — low participation reduces confidence in any price move seen recently.']);
    expect(buildSignalsSummary(vol(1), undefined, undefined, undefined)).toEqual([]);
  });

  it('skips gaps without targets, DAOI without shares, and calls without sweeps', () => {
    const lines = buildSignalsSummary(
      tech({ gap_analysis: { ...TECH.gap_analysis!, bounce_targets: [] } }),
      { ...FLOW, delta_adjusted_oi: { ...FLOW.delta_adjusted_oi!, net_daoi_shares: null as unknown as number },
        unusual_calls: { ...FLOW.unusual_calls!, unusual_calls: [] } },
      { ticker: 'X', drawdown: null },
      news('neutral', 0),
    );
    expect(lines).toEqual([]);
  });

  it('words negative and neutral news', () => {
    expect(buildSignalsSummary(undefined, undefined, undefined, news('negative'))[0]).toMatch(
      /is broadly negative .* — negative headlines may act as a near-term headwind\.$/);
    expect(buildSignalsSummary(undefined, undefined, undefined, news('neutral'))[0]).toMatch(
      /across 3 recent articles is mixed\/neutral .* — no strong directional bias from news\.$/);
  });
});
