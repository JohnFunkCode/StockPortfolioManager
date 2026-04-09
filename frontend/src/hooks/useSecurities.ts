import { useQuery } from '@tanstack/react-query';
import { securitiesApi } from '../api/securities';

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
