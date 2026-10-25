/** Shared payloads for the Signals-tab panel tests. */
import type { OptionsFlowResponse, RiskSignalsResponse, TechnicalSignalsResponse } from '../../api/securitiesTypes';

const pattern = (i: number, bias: string) => ({
  date: `2026-10-${10 + i}`, pattern: `pattern_${i}`, bias, strength: 'moderate',
  strength_score: 2, notes: [`note ${i}a`, `note ${i}b`],
});

export const TECH: TechnicalSignalsResponse = {
  ticker: 'INTC',
  stochastic: { k: 18.25, d: 30, signal: 'oversold', last_close: 100 },
  vwap: {
    vwap: 101.5, position: 'above_vwap', distance_pct: 1.24, reclaim_signal: true,
    reclaim_strength: 'strong', interpretation: 'VWAP note here',
  },
  obv: {
    divergence: 'bullish', divergence_strength: 'moderate', obv_trend: 'rising',
    price_trend: 'falling', interpretation: 'OBV note here', last_obv: 1,
  },
  volume_analysis: {
    bottom_signal: 'possible bottom', obv_divergence: true, last_volume_ratio: 2.5,
    climax_events: [
      { date: '2026-10-20', direction: 'down', volume_ratio: 3.1, interpretation: '', quiet_follow_through: false },
      { date: '2026-10-21', direction: 'up', volume_ratio: 2.2, interpretation: '', quiet_follow_through: false },
      { date: '2026-10-22', direction: 'up', volume_ratio: 2.0, interpretation: '', quiet_follow_through: false },
    ],
  },
  candlestick_patterns: {
    pattern_count: 6, bounce_signal: 'bullish bounce',
    patterns_found: [0, 1, 2, 3, 4, 5].map((i) => pattern(i, i % 2 ? 'bearish' : 'bullish')),
  },
  higher_lows: {
    higher_low_pattern: true, pattern_strength: 'strong', consecutive_higher_lows: 3,
    trend_before_lows: 'down', interpretation: 'Higher lows note', swing_lows: [],
  },
  gap_analysis: {
    unfilled_count: 2, partial_count: 1, interpretation: 'Gap note',
    bounce_targets: [
      { level: 'a', gap_bottom: 95, gap_top: 97, direction: 'gap_up', distance_pct: -2, note: '' },
      { level: 'b', gap_bottom: 110, gap_top: 112.5, direction: 'gap_down', distance_pct: 8.4, note: '' },
    ],
  },
  _errors: null,
};

const call = (i: number) => ({
  expiration: `2026-11-${10 + i}`, strike: 200 + i, last: 1.5, iv: 42.3, volume: 12000,
  open_interest: 4000, vol_oi_ratio: i === 0 ? 2.5 : 0.8, otm_pct: i === 0 ? -1.5 : 3.2,
  sweep_score: 5, conviction: i === 0 ? 'very high' : 'high', notes: [],
});

export const FLOW: OptionsFlowResponse = {
  ticker: 'INTC',
  _errors: null,
  delta_adjusted_oi: {
    net_daoi_shares: -1000, mm_hedge_bias: 'sell_on_rally', mm_note: 'MM net long',
    delta_flip_strike: 100, gamma_wall_strike: 105, dist_to_flip_pct: 1.5,
    signal: 'moderate', signal_note: '',
  },
  unusual_calls: {
    sweep_signal: 'bullish', interpretation: 'no unusual activity',
    unusual_calls: Array.from({ length: 9 }, (_, i) => call(i)),
  },
};

export const RISK: RiskSignalsResponse = {
  ticker: 'INTC',
  drawdown: {
    max_1day_drawdown_pct: -5, max_5day_drawdown_pct: -9, trailing_stop_pct: 8.5,
    max_intraday_drop_pct: -3.2, worst_1day_date: '2026-08-01', recent_max_1day_pct: -4.1,
    stop_width_note: 'note',
  },
};

/** A fetch that never answers: the query stays loading. */
export const pending = () => new Promise<never>(() => {});
