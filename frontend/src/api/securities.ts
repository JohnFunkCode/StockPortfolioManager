import { apiRequest } from './client';
import type {
  Security,
  OHLCVBar,
  TechnicalIndicator,
  OptionsSnapshot,
  OptionsPCHistory,
} from './securitiesTypes';

export interface SecuritiesResponse { securities: Security[] }
export interface OHLCVResponse { ticker: string; bars: OHLCVBar[] }
export interface TechnicalsResponse { ticker: string; indicators: TechnicalIndicator[] }
export interface OptionsLatestResponse { ticker: string; snapshot: OptionsSnapshot | null }
export interface OptionsPCHistoryResponse { ticker: string; history: OptionsPCHistory[] }

export const securitiesApi = {
  getAll: (source?: 'portfolio' | 'watchlist') => {
    const endpoint = source === 'portfolio'
      ? '/api/portfolio'
      : source === 'watchlist'
        ? '/api/watchlist'
        : '/api/securities';
    return apiRequest<SecuritiesResponse>(endpoint);
  },

  getOHLCV: (ticker: string, days = 180) =>
    apiRequest<OHLCVResponse>(`/api/securities/${ticker}/ohlcv?days=${days}`),

  getTechnicals: (ticker: string, days = 365) =>
    apiRequest<TechnicalsResponse>(`/api/securities/${ticker}/technicals?days=${days}`),

  getOptionsLatest: (ticker: string) =>
    apiRequest<OptionsLatestResponse>(`/api/securities/${ticker}/options/latest`),

  getOptionsHistory: (ticker: string, days = 30) =>
    apiRequest<OptionsPCHistoryResponse>(`/api/securities/${ticker}/options/history?days=${days}`),
};
