export interface Security {
  name: string;
  symbol: string;
  currency: string;
  source: 'portfolio' | 'watchlist' | 'both';
  tags: string[];
  purchase_price: number | null;
  quantity: number | null;
  purchase_date: string | null;
  sale_price: number | null;
  sale_date: string | null;
}

export interface OHLCVBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TechnicalIndicator {
  date: string;
  close: number | null;
  volume: number;
  ma10: number | null;
  ma30: number | null;
  ma50: number | null;
  ma100: number | null;
  ma200: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  rsi: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
}

export interface OptionsContract {
  contract_id: number;
  expiration_id: number;
  kind: 'call' | 'put';
  strike: number;
  last_price: number | null;
  bid: number | null;
  ask: number | null;
  implied_vol: number | null;
  volume: number | null;
  open_interest: number | null;
  in_the_money: 0 | 1;
}

export interface OptionsExpiration {
  expiration_id: number;
  snapshot_id: number;
  expiration: string;
  put_call_ratio: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  total_call_vol: number | null;
  total_put_vol: number | null;
  avg_call_iv: number | null;
  avg_put_iv: number | null;
  contracts: OptionsContract[];
}

export interface OptionsSnapshot {
  snapshot_id: number;
  symbol: string;
  captured_at: string;
  price: number;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  bb_period: number;
  expirations: OptionsExpiration[];
}

export interface OptionsPCHistory {
  captured_at: string;
  price: number;
  put_call_ratio: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
}

export interface PainPoint {
  strike: number;
  pain: number;
}

export interface OptionsExpirationAnalytics {
  expiration: string;
  max_pain: number | null;
  expected_move_dollar: number;
  expected_move_pct: number;
  atm_strike: number | null;
  upper_bound: number;
  lower_bound: number;
  total_call_oi: number;
  total_put_oi: number;
  put_call_ratio: number | null;
  pain_curve: PainPoint[];
}

export interface OptionsAnalyticsResponse {
  ticker: string;
  price: number;
  analytics: OptionsExpirationAnalytics[] | null;
  message?: string;
}

export interface IVRankResponse {
  ticker: string;
  current_iv: number | null;
  iv_rank: number | null;       // 0–100
  iv_percentile: number | null; // 0–100
  iv_52w_high: number | null;
  iv_52w_low: number | null;
  data_points: number;
  history: { captured_at: string; composite_iv: number }[];
}

// #3 Earnings
export interface EarningsResponse {
  ticker: string;
  earnings_dates: string[]; // YYYY-MM-DD
}

// #4 Technical signals
export interface StochasticData {
  k: number; d: number; signal: string; last_close: number;
}
export interface VWAPData {
  vwap: number; position: string; distance_pct: number;
  reclaim_signal: boolean; reclaim_strength: string; interpretation: string;
  consecutive_bars_above?: number; consecutive_bars_below?: number;
}
export interface OBVData {
  divergence: string; divergence_strength: string;
  obv_trend: string; price_trend: string; interpretation: string;
  last_obv: number;
}
export interface VolumeAnalysisData {
  bottom_signal: string; obv_divergence: boolean;
  last_volume_ratio: number; climax_events: {
    date: string; direction: string; volume_ratio: number; interpretation: string; quiet_follow_through: boolean;
  }[];
}
export interface CandlestickPatternsData {
  pattern_count: number; bounce_signal: string;
  patterns_found: {
    date: string; pattern: string; bias: string; strength: string;
    strength_score: number; notes: string[];
  }[];
}
export interface HigherLowsData {
  higher_low_pattern: boolean; pattern_strength: string;
  consecutive_higher_lows: number; trend_before_lows: string;
  interpretation: string;
  swing_lows: { date: string; low: number; close: number }[];
}
export interface GapAnalysisData {
  unfilled_count: number; partial_count: number; interpretation: string;
  bounce_targets: {
    level: string; gap_bottom: number; gap_top: number;
    direction: string; distance_pct: number; note: string;
  }[];
}

export interface TechnicalSignalsResponse {
  ticker: string;
  stochastic: StochasticData | null;
  vwap: VWAPData | null;
  obv: OBVData | null;
  volume_analysis: VolumeAnalysisData | null;
  candlestick_patterns: CandlestickPatternsData | null;
  higher_lows: HigherLowsData | null;
  gap_analysis: GapAnalysisData | null;
  _errors?: Record<string, string> | null;
}

export interface OptionsFlowResponse {
  ticker: string;
  _errors?: Record<string, string> | null;
  unusual_calls: {
    sweep_signal: string; interpretation: string;
    unusual_calls: {
      expiration: string; strike: number; last: number;
      iv: number; volume: number; open_interest: number;
      vol_oi_ratio: number; otm_pct: number;
      sweep_score: number; conviction: string; notes: string[];
    }[];
  } | null;
  delta_adjusted_oi: {
    net_daoi_shares: number; mm_hedge_bias: string; mm_note: string;
    delta_flip_strike: number | null; gamma_wall_strike: number | null;
    dist_to_flip_pct: number | null; signal: string; signal_note: string;
  } | null;
}

export interface RiskSignalsResponse {
  ticker: string;
  drawdown: {
    max_1day_drawdown_pct: number; max_5day_drawdown_pct: number;
    trailing_stop_pct: number; max_intraday_drop_pct: number;
    worst_1day_date: string; recent_max_1day_pct: number;
    stop_width_note: string;
  } | null;
  vwap?: number;
  vwap_position?: string;
}

// #5 Portfolio delta exposure
export interface PortfolioDeltaPosition {
  symbol: string; name: string; price: number; shares: number;
  stock_delta: number; net_daoi_shares: number;
  call_daoi: number; put_daoi: number;
  mm_hedge_bias: string; captured_at: string | null;
}
export interface PortfolioDeltaResponse {
  portfolio_net_daoi: number;
  positions: PortfolioDeltaPosition[];
}

// Add security
export interface AddSecurityPayload {
  symbol: string;
  name?: string;
  currency?: string;
  // watchlist only
  tags?: string[];
  // portfolio only
  purchase_price?: number | null;
  quantity?: number | null;
  purchase_date?: string | null;
}
export interface AddSecurityResponse {
  symbol: string;
  destination: 'watchlist' | 'portfolio';
}

// Polygon.io historical backfill
export interface BackfillResult {
  date: string;
  status: 'stored' | 'duplicate' | 'no_data' | 'no_expirations' | 'error';
  expirations?: number;
  contracts?: number;
  price?: number;
  error?: string;
}
export interface BackfillResponse {
  ticker: string;
  days_requested: number;
  dates_attempted: number;
  stored: number;
  skipped: number;
  no_data: number;
  failed: number;
  results: BackfillResult[];
}

// Bulk options snapshot refresh
export interface SnapshotRefreshResult {
  symbol: string;
  status: 'ok' | 'error';
  error?: string;
}
export interface SnapshotRefreshResponse {
  source: string;
  chain_type: string;
  total: number;
  succeeded: number;
  failed: number;
  duration_seconds: number;
  results: SnapshotRefreshResult[];
  note: string;
}

// #6 News + FinBERT sentiment
export interface NewsArticle {
  title: string;
  publisher: string;
  published: string;
  summary: string;
  url: string;
  sentiment?: 'positive' | 'negative' | 'neutral';
  sentiment_score?: number;
}
export interface NewsSentimentSummary {
  overall: 'positive' | 'negative' | 'neutral';
  positive_count: number;
  negative_count: number;
  neutral_count: number;
  scored_count: number;
}
export interface NewsResponse {
  symbol: string;
  article_count: number;
  articles: NewsArticle[];
  sentiment_summary?: NewsSentimentSummary;
  sentiment_note?: string;
}

// #7 Screener
export interface ScreenerResult extends Security {
  last_close: number;
  rsi: number | null;
  ma50: number | null;
  ma200: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
  macd: number | null;
  macd_signal: number | null;
}
export interface ScreenerResponse {
  results: ScreenerResult[];
  count: number;
}
