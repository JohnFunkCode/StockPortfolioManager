import { useQuery } from '@tanstack/react-query';
import { symbolsApi } from '../api/symbols';

export function useSymbols() {
  return useQuery({
    queryKey: ['symbols'],
    queryFn: symbolsApi.list,
  });
}

export function usePricePolling(ticker: string, interval = 30000) {
  return useQuery({
    queryKey: ['price', ticker],
    queryFn: () => symbolsApi.getPrice(ticker),
    refetchInterval: interval,
    enabled: !!ticker,
  });
}
