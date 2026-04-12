import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { securitiesApi } from '../api/securities';
import type { AddSecurityPayload } from '../api/securitiesTypes';

export function useSecurities(source?: 'portfolio' | 'watchlist') {
  return useQuery({
    queryKey: ['securities', source ?? 'all'],
    queryFn: () => securitiesApi.getAll(source),
    staleTime: 5 * 60 * 1000,
  });
}

export function useOHLCV(ticker: string, days = 180) {
  return useQuery({
    queryKey: ['ohlcv', ticker, days],
    queryFn: () => securitiesApi.getOHLCV(ticker, days),
    enabled: !!ticker,
    staleTime: 10 * 60 * 1000,
  });
}

export function useTechnicals(ticker: string, days = 365) {
  return useQuery({
    queryKey: ['technicals', ticker, days],
    queryFn: () => securitiesApi.getTechnicals(ticker, days),
    enabled: !!ticker,
    staleTime: 10 * 60 * 1000,
  });
}

export function useOptionsLatest(ticker: string) {
  return useQuery({
    queryKey: ['options-latest', ticker],
    queryFn: () => securitiesApi.getOptionsLatest(ticker),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOptionsHistory(ticker: string, days = 30) {
  return useQuery({
    queryKey: ['options-history', ticker, days],
    queryFn: () => securitiesApi.getOptionsHistory(ticker, days),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOptionsAnalytics(ticker: string) {
  return useQuery({
    queryKey: ['options-analytics', ticker],
    queryFn: () => securitiesApi.getOptionsAnalytics(ticker),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  });
}

export function useIVRank(ticker: string) {
  return useQuery({
    queryKey: ['iv-rank', ticker],
    queryFn: () => securitiesApi.getIVRank(ticker),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  });
}

export function useEarnings(ticker: string) {
  return useQuery({
    queryKey: ['earnings', ticker],
    queryFn: () => securitiesApi.getEarnings(ticker),
    enabled: !!ticker,
    staleTime: 60 * 60 * 1000, // 1 hour — earnings dates rarely change
  });
}

export function useTechnicalSignals(ticker: string, enabled = true) {
  return useQuery({
    queryKey: ['signals-technical', ticker],
    queryFn: () => securitiesApi.getTechnicalSignals(ticker),
    enabled: !!ticker && enabled,
    staleTime: 15 * 60 * 1000,
  });
}

export function useOptionsFlowSignals(ticker: string, enabled = true) {
  return useQuery({
    queryKey: ['signals-options-flow', ticker],
    queryFn: () => securitiesApi.getOptionsFlowSignals(ticker),
    enabled: !!ticker && enabled,
    staleTime: 10 * 60 * 1000,
  });
}

export function useRiskSignals(ticker: string, enabled = true) {
  return useQuery({
    queryKey: ['signals-risk', ticker],
    queryFn: () => securitiesApi.getRiskSignals(ticker),
    enabled: !!ticker && enabled,
    staleTime: 30 * 60 * 1000,
  });
}

export function useNews(ticker: string, maxArticles = 10) {
  return useQuery({
    queryKey: ['news', ticker, maxArticles],
    queryFn: () => securitiesApi.getNews(ticker, maxArticles),
    enabled: !!ticker,
    staleTime: 15 * 60 * 1000,
  });
}

export function useSentimentSummary(source: 'portfolio' | 'watchlist' | 'all' = 'all') {
  return useQuery({
    queryKey: ['sentiment-summary', source],
    queryFn: () => securitiesApi.getSentimentSummary(source),
    staleTime: 10 * 60 * 1000,
  });
}

export function usePortfolioDeltaExposure() {
  return useQuery({
    queryKey: ['portfolio-delta-exposure'],
    queryFn: () => securitiesApi.getPortfolioDeltaExposure(),
    staleTime: 10 * 60 * 1000,
  });
}

export function useScreener(params: Record<string, string>, enabled = false) {
  return useQuery({
    queryKey: ['screener', params],
    queryFn: () => securitiesApi.screenSecurities(params),
    enabled,
    staleTime: 5 * 60 * 1000,
  });
}

export function useRefreshOptionsSnapshots() {
  return useMutation({
    mutationFn: ({
      source = 'all',
      chainType = 'atm',
    }: { source?: 'portfolio' | 'watchlist' | 'all'; chainType?: 'atm' | 'full' } = {}) =>
      securitiesApi.refreshOptionsSnapshots(source, chainType),
  });
}

export function useBackfillOptionsHistory(ticker: string) {
  return useMutation({
    mutationFn: ({ days = 90 }: { days?: number } = {}) =>
      securitiesApi.backfillOptionsHistory(ticker, days),
  });
}

export function useRemoveFromPortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ticker: string) => securitiesApi.removeFromPortfolio(ticker),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['securities'] }),
  });
}

export function useAddSecurity() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['securities'] });
  };
  const watchlist = useMutation({
    mutationFn: (payload: AddSecurityPayload) => securitiesApi.addToWatchlist(payload),
    onSuccess: invalidate,
  });
  const portfolio = useMutation({
    mutationFn: (payload: AddSecurityPayload) => securitiesApi.addToPortfolio(payload),
    onSuccess: invalidate,
  });
  return { watchlist, portfolio };
}
