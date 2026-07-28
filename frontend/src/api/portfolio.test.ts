/**
 * Endpoint-shape tests for portfolioApi methods: assert URL, method, query
 * string, and body.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { portfolioApi } from './portfolio';
import { mockApi } from '../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('portfolioApi GET endpoints', () => {
  it('getLots hits /api/portfolio/lots with no force query by default', async () => {
    const api = mockApi([[/\/api\/portfolio\/lots/, { lots: [] }]]);
    await portfolioApi.getLots();
    expect(api.calls[0][0]).toContain('/api/portfolio/lots');
    expect(api.calls[0][0]).not.toContain('force');
  });

  it('getLots appends force=true when requested', async () => {
    const api = mockApi([[/\/api\/portfolio\/lots/, { lots: [] }]]);
    await portfolioApi.getLots(true);
    expect(api.calls[0][0]).toContain('/api/portfolio/lots?force=true');
  });

  it('getSymbolRows hits /api/portfolio/symbols', async () => {
    const api = mockApi([[/\/api\/portfolio\/symbols/, { symbols: [] }]]);
    await portfolioApi.getSymbolRows();
    expect(api.calls[0][0]).toContain('/api/portfolio/symbols');
    expect(api.calls[0][0]).not.toContain('force');
  });

  it('getSymbolRows appends force=true when requested', async () => {
    const api = mockApi([[/\/api\/portfolio\/symbols/, { symbols: [] }]]);
    await portfolioApi.getSymbolRows(true);
    expect(api.calls[0][0]).toContain('/api/portfolio/symbols?force=true');
  });
});

describe('portfolioApi mutations', () => {
  it('createLot POSTs the payload to /api/portfolio/lots', async () => {
    const api = mockApi([[/\/api\/portfolio\/lots/, { symbol: 'INTC' }]]);
    await portfolioApi.createLot({ symbol: 'INTC', quantity: 10, purchase_price: 30 });
    expect(api.calls[0][0]).toContain('/api/portfolio/lots');
    expect(api.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(api.calls[0][1]?.body)).symbol).toBe('INTC');
  });

  it('updateLot PATCHes /api/portfolio/lots/{lot_id}', async () => {
    const api = mockApi([[/\/api\/portfolio\/lots\/42/, { lot_id: 42, updated: true }]]);
    await portfolioApi.updateLot(42, { notes: 'fixed typo' });
    expect(api.calls[0][0]).toContain('/api/portfolio/lots/42');
    expect(api.calls[0][1]?.method).toBe('PATCH');
    expect(JSON.parse(String(api.calls[0][1]?.body)).notes).toBe('fixed typo');
  });

  it('deleteLot DELETEs /api/portfolio/lots/{lot_id}', async () => {
    const api = mockApi([[/\/api\/portfolio\/lots\/42/, { lot_id: 42, deleted: true }]]);
    await portfolioApi.deleteLot(42);
    expect(api.calls[0][0]).toContain('/api/portfolio/lots/42');
    expect(api.calls[0][1]?.method).toBe('DELETE');
  });

  it('closeLot POSTs to /api/portfolio/lots/{lot_id}/close', async () => {
    const api = mockApi([[/\/api\/portfolio\/lots\/42\/close/, { symbol: 'INTC', allocations: [] }]]);
    await portfolioApi.closeLot(42, {
      shares: 5,
      sale_price: 35,
      sale_trade_date: '2026-07-20',
    });
    expect(api.calls[0][0]).toContain('/api/portfolio/lots/42/close');
    expect(api.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(api.calls[0][1]?.body)).shares).toBe(5);
  });
});
