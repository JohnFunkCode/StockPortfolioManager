import { apiRequest } from './client';
import type {
  Security,
  OHLCVBar,
  TechnicalIndicator,
  OptionsSnapshot,
  OptionsPCHistory,
  OptionsAnalyticsResponse,
  IVRankResponse,
  EarningsResponse,
  TechnicalSignalsResponse,
  OptionsFlowResponse,
  RiskSignalsResponse,
  PortfolioDeltaResponse,
  ScreenerResponse,
  SnapshotRefreshResponse,
  BackfillResponse,
  AddSecurityPayload,
  AddSecurityResponse,
} from './securitiesTypes';

export interface SecuritiesResponse { securities: Security[] }
export interface OHLCVResponse { ticker: string; bars: OHLCVBar[] }
export interface TechnicalsResponse { ticker: string; indicators: TechnicalIndicator[] }
export interface OptionsLatestResponse { ticker: string; snapshot: OptionsSnapshot | null }
export interface OptionsPCHistoryResponse { ticker: string; history: OptionsPCHistory[] }
export type {
  OptionsAnalyticsResponse, IVRankResponse, EarningsResponse,
  TechnicalSignalsResponse, OptionsFlowResponse, RiskSignalsResponse,
  PortfolioDeltaResponse, ScreenerResponse, SnapshotRefreshResponse, BackfillResponse,
  AddSecurityPayload, AddSecurityResponse,
};

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

  getOptionsAnalytics: (ticker: string) =>
    apiRequest<OptionsAnalyticsResponse>(`/api/securities/${ticker}/options/analytics`),

  getIVRank: (ticker: string) =>
    apiRequest<IVRankResponse>(`/api/securities/${ticker}/options/iv-rank`),

  getEarnings: (ticker: string) =>
    apiRequest<EarningsResponse>(`/api/securities/${ticker}/earnings`),

  getTechnicalSignals: (ticker: string) =>
    apiRequest<TechnicalSignalsResponse>(`/api/securities/${ticker}/signals/technical`),

  getOptionsFlowSignals: (ticker: string) =>
    apiRequest<OptionsFlowResponse>(`/api/securities/${ticker}/signals/options-flow`),

  getRiskSignals: (ticker: string) =>
    apiRequest<RiskSignalsResponse>(`/api/securities/${ticker}/signals/risk`),

  getPortfolioDeltaExposure: () =>
    apiRequest<PortfolioDeltaResponse>('/api/portfolio/delta-exposure'),

  screenSecurities: (params: Record<string, string>) => {
    const qs = new URLSearchParams(params).toString();
    return apiRequest<ScreenerResponse>(`/api/securities/screen${qs ? `?${qs}` : ''}`);
  },

  refreshOptionsSnapshots: (source: 'portfolio' | 'watchlist' | 'all' = 'all', chainType: 'atm' | 'full' = 'atm') =>
    apiRequest<SnapshotRefreshResponse>(
      `/api/securities/refresh-options-snapshots?source=${source}&chain_type=${chainType}`,
      { method: 'POST' },
    ),

  backfillOptionsHistory: (ticker: string, days = 90) =>
    apiRequest<BackfillResponse>(
      `/api/securities/${ticker}/options/history/backfill?days=${days}`,
      { method: 'POST' },
    ),

  addToWatchlist: (payload: AddSecurityPayload) =>
    apiRequest<AddSecurityResponse>('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  addToPortfolio: (payload: AddSecurityPayload) =>
    apiRequest<AddSecurityResponse>('/api/portfolio', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
