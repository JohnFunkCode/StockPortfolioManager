import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { securitiesApi } from '../api/securities';
import type { AddSecurityPayload, StopLossConfluence } from '../api/securitiesTypes';

// `source` is required: the combined list (GET /api/securities) was retired
// with the Securities page (#224) — the merged list is useWatchlistMine.
// `enabled` exists for callers that mount the hook long before they need it —
// CreatePlanDialog stays mounted while shut, and a closed dialog should not be
// fetching a symbol list nobody is looking at.
export function useSecurities(source: 'portfolio' | 'watchlist', enabled = true) {
  return useQuery({
    queryKey: ['securities', source],
    queryFn: () => securitiesApi.getAll(source),
    enabled,
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

export function useSupportConfluence(ticker: string, enabled = true) {
  return useQuery({
    queryKey: ['support-confluence', ticker],
    queryFn: () => securitiesApi.getSupportConfluence(ticker),
    enabled: !!ticker && enabled,
    // The endpoint fans out to ~14 sub-analyses server-side — cache generously.
    staleTime: 15 * 60 * 1000,
  });
}

// With confluence='full' the response embeds the support-confluence result
// (same tolerance and zone count as useSupportConfluence's defaults), so it
// seeds that query: the Technical Analysis tab gets both panels from one
// request instead of gathering the 14 level sources twice (#399).
export function useStopLoss(ticker: string, confluence: StopLossConfluence = 'stop') {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ['stop-loss', ticker, confluence],
    queryFn: async () => {
      const result = await securitiesApi.getStopLoss(ticker, confluence);
      if (result.confluence) qc.setQueryData(['support-confluence', ticker], result.confluence);
      return result;
    },
    enabled: !!ticker,
    staleTime: 15 * 60 * 1000,
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

export function useSentimentTrend(ticker: string, weeks: number) {
  return useQuery({
    queryKey: ['sentiment-trend', ticker, weeks],
    queryFn: () => securitiesApi.getSentimentTrend(ticker, weeks),
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['securities'] });
      // The Watchlist page's Held chip and Qty/Avg Price come from /watchlist/mine.
      qc.invalidateQueries({ queryKey: ['watchlist-mine'] });
    },
  });
}

// The removed row drops off the /watchlist page, which reads ['watchlist-mine']
// (and the older ['watchlist-fundamentals'] for any other reader): the mutation
// is shared, so the invalidation has to cover every key or the page the user
// removed from is the one that keeps showing the row.
export function useRemoveFromWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ticker: string) => securitiesApi.removeFromWatchlist(ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['securities'] });
      qc.invalidateQueries({ queryKey: ['watchlist-fundamentals'] });
      qc.invalidateQueries({ queryKey: ['watchlist-mine'] });
    },
  });
}

export function useAddSecurity() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['securities'] });
    // Same dialog adds from the /watchlist page — see useRemoveFromWatchlist.
    qc.invalidateQueries({ queryKey: ['watchlist-fundamentals'] });
    qc.invalidateQueries({ queryKey: ['watchlist-mine'] });
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

export function useSymbolLookup(symbol: string) {
  return useQuery({
    queryKey: ['symbol-lookup', symbol],
    queryFn: () => securitiesApi.lookupSymbol(symbol),
    enabled: symbol.length > 0,
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
}

export function useVerticalSpread(
  ticker: string,
  expiration: string,
  longStrike: number,
  shortStrike: number,
  kind: string,
) {
  return useQuery({
    queryKey: ['vertical-spread', ticker, expiration, longStrike, shortStrike, kind],
    queryFn: () =>
      securitiesApi.priceVerticalSpread(ticker, {
        expiration,
        long_strike: longStrike,
        short_strike: shortStrike,
        kind,
        // The chart draws server-computed curves (issue #79) — only the UI
        // opts in; LLM-facing tool calls stay lean.
        include_curves: true,
      }),
    enabled: !!ticker && !!expiration && longStrike > 0 && shortStrike > 0,
    staleTime: 60 * 1000,
    retry: 1,
  });
}
