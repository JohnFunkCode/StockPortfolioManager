import { useQuery } from '@tanstack/react-query';
import { agentsApi } from '../api/agents';
import type { SignalsFilter } from '../api/agents';

export function useAgentSignals(filter: SignalsFilter = {}) {
  return useQuery({
    queryKey: ['agent-signals', filter],
    queryFn:  () => agentsApi.getSignals(filter),
    staleTime: 60_000,
  });
}

export function useAgentRecommendations(symbol?: string, limit = 20) {
  return useQuery({
    queryKey: ['agent-recommendations', symbol, limit],
    queryFn:  () => agentsApi.getRecommendations(symbol, limit),
    staleTime: 60_000,
  });
}

export function useAgentHealth() {
  return useQuery({
    queryKey: ['agent-health'],
    queryFn:  agentsApi.getHealth,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
