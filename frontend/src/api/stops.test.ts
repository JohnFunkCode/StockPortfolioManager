/** Endpoint-shape tests for stopsApi: the URL and method each call uses. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { stopsApi } from './stops';
import { holdingStops, mockApi, stopRow, stopsResponse } from '../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('stopsApi', () => {
  it('reads the real book with no book id and a named book with one', async () => {
    const api = mockApi([
      ['/api/portfolio/stops/holdings', holdingStops()],
      ['/api/portfolio/stops', stopsResponse()],
    ]);
    await stopsApi.list();
    await stopsApi.list(null, true);
    await stopsApi.list(7);
    const answer = await stopsApi.list(7, true);
    await stopsApi.holdings();
    const held = await stopsApi.holdings(7);

    expect(api.calls.map(([url]) => url)).toEqual([
      '/api/portfolio/stops',
      '/api/portfolio/stops?include_inactive=true',
      '/api/portfolio/stops?book_id=7',
      '/api/portfolio/stops?include_inactive=true&book_id=7',
      '/api/portfolio/stops/holdings',
      '/api/portfolio/stops/holdings?book_id=7',
    ]);
    expect(api.calls.every(([, init]) => init?.method === undefined || init.method === 'GET')).toBe(true);
    expect(answer.stops[0].stop_level).toBe(45);
    expect(held.stops[0].symbol).toBe('INTC');
  });

  it('cancels a stop with a POST, naming the book only when there is one', async () => {
    const api = mockApi([[/\/stops\/\d+\/cancel/, stopRow({ status: 'cancelled' })]]);
    await stopsApi.cancel(3);
    const cancelled = await stopsApi.cancel(4, 7);

    expect(api.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/portfolio/stops/3/cancel', 'POST'],
      ['/api/portfolio/stops/4/cancel?book_id=7', 'POST'],
    ]);
    expect(api.calls.every(([, init]) => init?.body === undefined)).toBe(true);
    expect(cancelled.status).toBe('cancelled');
  });
});
