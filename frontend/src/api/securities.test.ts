/**
 * Endpoint-shape tests for securitiesApi methods not already exercised through
 * the hook suites: assert URL, method, query-string encoding, and body.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { securitiesApi } from './securities';
import { mockApi } from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('securitiesApi GET endpoints', () => {
  it('getAll picks the endpoint by source', async () => {
    const api = mockApi([[/\/api\/(securities|portfolio|watchlist)/, { securities: [] }]]);
    await securitiesApi.getAll();
    await securitiesApi.getAll('portfolio');
    await securitiesApi.getAll('watchlist');
    expect(api.calls[0][0]).toContain('/api/securities');
    expect(api.calls[1][0]).toContain('/api/portfolio');
    expect(api.calls[2][0]).toContain('/api/watchlist');
  });

  it('detail GETs forward their ids and query params', async () => {
    const api = mockApi([[/./, { ok: true }]]);
    await securitiesApi.getOptionsHistory('INTC', 45);
    await securitiesApi.getOptionsAnalytics('INTC');
    await securitiesApi.getIVRank('INTC');
    await securitiesApi.getEarnings('INTC');
    await securitiesApi.getRiskSignals('INTC');
    await securitiesApi.getSupportConfluence('INTC');
    await securitiesApi.getPortfolioDeltaExposure();
    expect(api.calls[0][0]).toContain('/options/history?days=45');
    expect(api.calls[1][0]).toContain('/options/analytics');
    expect(api.calls[2][0]).toContain('/options/iv-rank');
    expect(api.calls[3][0]).toContain('/earnings');
    expect(api.calls[4][0]).toContain('/signals/risk');
    expect(api.calls[5][0]).toContain('/support-confluence');
    expect(api.calls[6][0]).toContain('/api/portfolio/delta-exposure');
  });

  it('screenSecurities encodes query params', async () => {
    const api = mockApi([['/screen', { results: [] }]]);
    await securitiesApi.screenSecurities({ rsi_max: '40', above_ma50: 'true' });
    expect(api.calls[0][0]).toContain('rsi_max=40');
    expect(api.calls[0][0]).toContain('above_ma50=true');
  });

  it('lookupSymbol url-encodes the symbol', async () => {
    const api = mockApi([['/lookup', { symbol: 'BRK.B' }]]);
    await securitiesApi.lookupSymbol('BRK.B');
    expect(api.calls[0][0]).toContain('symbol=BRK.B');
  });

  it('getSentimentSummary and getNews carry their params', async () => {
    const api = mockApi([[/./, { ok: true }]]);
    await securitiesApi.getNews('INTC', 5);
    await securitiesApi.getSentimentSummary('portfolio');
    expect(api.calls[0][0]).toContain('/news?max_articles=5');
    expect(api.calls[1][0]).toContain('source=portfolio');
  });
});

describe('securitiesApi mutations', () => {
  it('add to watchlist/portfolio POST the payload', async () => {
    const api = mockApi([[/\/api\/(watchlist|portfolio)/, { added: true }]]);
    await securitiesApi.addToWatchlist({ symbol: 'INTC' } as never);
    await securitiesApi.addToPortfolio({ symbol: 'WMT' } as never);
    expect(api.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(api.calls[0][1]?.body)).symbol).toBe('INTC');
    expect(api.calls[1][0]).toContain('/api/portfolio');
  });

  it('backfillOptionsHistory POSTs with days', async () => {
    const api = mockApi([['/history/backfill', { backfilled: 0 }]]);
    await securitiesApi.backfillOptionsHistory('INTC', 120);
    expect(api.calls[0][0]).toContain('days=120');
    expect(api.calls[0][1]?.method).toBe('POST');
  });
});
