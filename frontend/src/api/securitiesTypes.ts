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
