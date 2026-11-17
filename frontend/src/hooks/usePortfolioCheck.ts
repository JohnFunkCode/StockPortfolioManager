import { useQuery } from '@tanstack/react-query';
import { portfolioCheckApi } from '../api/portfolioCheck';

/**
 * The caller's Portfolio Check. One query key for the page and both Sidekick
 * cards, so showing both costs one request. The market side changes monthly
 * and the decisions only move with prices, so a few minutes' staleness is fine.
 */
export function usePortfolioCheck() {
  return useQuery({
    queryKey: ['portfolio-check'],
    queryFn: portfolioCheckApi.getCheck,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}
