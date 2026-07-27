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

export function useSpreadHistory(security: string, days = 365) {
  return useQuery({
    queryKey: ['arbitrage-spread', security, days],
    queryFn: () => arbitrageApi.getSpreadHistory(security, days),
    enabled: !!security,
    staleTime: 10 * 60 * 1000,
  });
}

export function usePremiumHistory(security: string, days = 365) {
  return useQuery({
    queryKey: ['arbitrage-premium', security, days],
    queryFn: () => arbitrageApi.getPremiumHistory(security, days),
    enabled: !!security,
    staleTime: 10 * 60 * 1000,
  });
}

export function useArbitrageScan(topN = 20, days = 365) {
  return useQuery({
    queryKey: ['arbitrage-scan', topN, days],
    queryFn: () => arbitrageApi.scan(topN, days),
    // A cold scan is ten full pair workups — tens of seconds. Cache it hard;
    // the underlying data is daily bars, so a stale window costs nothing.
    staleTime: 30 * 60 * 1000,
    retry: false,
  });
}

/** Discovery is explicitly user-triggered — `enabled` gates the sweep. */
export function useDiscovery(symbols: string, enabled: boolean, requireLink = true) {
  return useQuery({
    queryKey: ['arbitrage-discovery', symbols, requireLink],
    queryFn: () => arbitrageApi.discover(symbols, requireLink),
    enabled: enabled && !!symbols.trim(),
    staleTime: 30 * 60 * 1000,
    retry: false,
  });
}
