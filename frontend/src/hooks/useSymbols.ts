import { useQuery } from '@tanstack/react-query';
import { symbolsApi } from '../api/symbols';

export function usePricePolling(ticker: string, interval = 30000) {
  return useQuery({
    queryKey: ['price', ticker],
    queryFn: () => symbolsApi.getPrice(ticker),
    refetchInterval: interval,
    enabled: !!ticker,
  });
}
