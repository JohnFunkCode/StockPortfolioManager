import { useQuery } from '@tanstack/react-query';
import { arbitrageApi } from '../api/arbitrage';

export function useArbitragePair(security: string, days = 365) {
  return useQuery({
    queryKey: ['arbitrage-pair', security, days],
    queryFn: () => arbitrageApi.getPair(security, days),
    enabled: !!security,
    // The workup pulls two price histories and (for NAV vehicles) a share
    // count; nothing in it moves intraday enough to justify a tighter window.
    staleTime: 10 * 60 * 1000,
  });
}
