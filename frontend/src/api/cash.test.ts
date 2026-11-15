/** Endpoint-shape tests for cashApi: URL, method and body. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { cashApi } from './cash';
import { cashLedger, mockApi } from '../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('cashApi', () => {
  it('reads the real book with no book id and a named book with one', async () => {
    const api = mockApi([['/api/portfolio/cash', cashLedger()]]);
    await cashApi.getLedger();
    await cashApi.getLedger(null);
    await cashApi.getLedger(7);

    expect(api.calls.map(([url]) => url)).toEqual([
      '/api/portfolio/cash',
      '/api/portfolio/cash',
      '/api/portfolio/cash?book_id=7',
    ]);
  });

  it('writes through the opening-balance and entries routes', async () => {
    const api = mockApi([['/api/portfolio/cash', cashLedger()]]);
    await cashApi.setOpeningBalance({ date: '2026-01-01', amount: 1000 });
    await cashApi.addEntry({ kind: 'withdrawal', date: '2026-02-01', amount: 40, notes: 'rent' });
    await cashApi.deleteEntry(12);

    expect(api.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/portfolio/cash/opening-balance', 'PUT'],
      ['/api/portfolio/cash/entries', 'POST'],
      ['/api/portfolio/cash/entries/12', 'DELETE'],
    ]);
    expect(JSON.parse(String(api.calls[0][1]?.body))).toEqual({ date: '2026-01-01', amount: 1000 });
    expect(JSON.parse(String(api.calls[1][1]?.body))).toEqual({
      kind: 'withdrawal', date: '2026-02-01', amount: 40, notes: 'rent',
    });
  });
});
