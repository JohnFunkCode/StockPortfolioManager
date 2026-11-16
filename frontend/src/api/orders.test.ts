/** Endpoint-shape tests for ordersApi: URL, method and body. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { newIdempotencyKey, ordersApi } from './orders';
import { mockApi, order, ordersResponse, seedResult } from '../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('ordersApi', () => {
  it('lists, places and cancels through the orders routes', async () => {
    const api = mockApi([
      [/\/api\/portfolio\/orders\?book_id=7$/, ordersResponse()],
      ['/api/portfolio/orders', order()],
    ]);
    await ordersApi.list(7);
    await ordersApi.place({ book_id: 7, symbol: 'INTC', side: 'buy', amount: 500, idempotency_key: 'k1' });
    await ordersApi.cancel(7, 12);

    expect(api.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/portfolio/orders?book_id=7', undefined],
      ['/api/portfolio/orders', 'POST'],
      ['/api/portfolio/orders/12/cancel?book_id=7', 'POST'],
    ]);
    expect(JSON.parse(String(api.calls[1][1]?.body))).toEqual({
      book_id: 7, symbol: 'INTC', side: 'buy', amount: 500, idempotency_key: 'k1',
    });
    expect(api.calls[2][1]?.body).toBeUndefined();
  });

  it('seeds a book and closes a position by hand, sending no price', async () => {
    const api = mockApi([
      ['/api/portfolio/orders/seed', seedResult()],
      ['/api/portfolio/orders/manual-close', order({ kind: 'manual_close' })],
    ]);
    await ordersApi.seed(7);
    await ordersApi.manualClose({ book_id: 7, symbol: 'INTC', idempotency_key: 'k2' });

    expect(api.calls.map(([url, init]) => [url, init?.method, JSON.parse(String(init?.body))])).toEqual([
      ['/api/portfolio/orders/seed', 'POST', { book_id: 7 }],
      ['/api/portfolio/orders/manual-close', 'POST', { book_id: 7, symbol: 'INTC', idempotency_key: 'k2' }],
    ]);
  });

  it('makes a new idempotency key each time', () => {
    expect(newIdempotencyKey()).not.toBe(newIdempotencyKey());
  });
});
