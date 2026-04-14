import { apiRequest } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentSignal {
  id: string;
  symbol: string;
  score: number;           // -9 to +9
  direction: 'buy' | 'sell' | 'neutral';
  triggers: Record<string, unknown>;
  escalated: boolean;
  fired_at: string | null;
}

export interface AgentRecommendation {
  id: string;
  symbol: string;
  recommendation: 'BUY' | 'SELL' | 'HOLD' | 'AVOID';
  conviction: 'HIGH' | 'MEDIUM' | 'LOW';
  entry_low: number | null;
  entry_high: number | null;
  price_target: number | null;
  stop_loss: number | null;
  details: {
    score?: number;
    bull_case?: string[];
    bear_case?: string[];
    options_play?: string;
    phases?: Record<string, unknown>;
  };
  fired_at: string | null;
}

export interface CircuitBreakerState {
  state: 'open' | 'closed';
  error_count?: number;
  resets_in_seconds?: number;
}

export interface AgentHealth {
  market_open: boolean;
  circuit_breakers: Record<string, CircuitBreakerState>;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Response wrappers
// ---------------------------------------------------------------------------

export interface SignalsResponse {
  signals: AgentSignal[];
  count: number;
}

export interface RecommendationsResponse {
  recommendations: AgentRecommendation[];
  count: number;
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export interface SignalsFilter {
  symbol?: string;
  direction?: 'buy' | 'sell' | 'neutral';
  days?: number;
  limit?: number;
}

export const agentsApi = {
  getSignals: (filter: SignalsFilter = {}) => {
    const params = new URLSearchParams();
    if (filter.symbol)    params.set('symbol', filter.symbol);
    if (filter.direction) params.set('direction', filter.direction);
    if (filter.days)      params.set('days', String(filter.days));
    if (filter.limit)     params.set('limit', String(filter.limit));
    const qs = params.toString();
    return apiRequest<SignalsResponse>(`/api/agents/signals${qs ? `?${qs}` : ''}`);
  },

  getRecommendations: (symbol?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (symbol) params.set('symbol', symbol);
    return apiRequest<RecommendationsResponse>(`/api/agents/recommendations?${params}`);
  },

  getHealth: () =>
    apiRequest<AgentHealth>('/api/agents/health'),
};
