/** Endpoint-shape tests for stopsApi: the URL each read asks for. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { stopsApi } from './stops';
import { mockApi, stopsResponse } from '../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('stopsApi', () => {
  it('reads the real book with no book id and a named book with one', async () => {
    const api = mockApi([['/api/portfolio/stops', stopsResponse()]]);
    await stopsApi.list();
    await stopsApi.list(null, true);
    await stopsApi.list(7);
    const answer = await stopsApi.list(7, true);

    expect(api.calls.map(([url]) => url)).toEqual([
      '/api/portfolio/stops',
      '/api/portfolio/stops?include_inactive=true',
      '/api/portfolio/stops?book_id=7',
      '/api/portfolio/stops?book_id=7&include_inactive=true',
    ]);
    expect(api.calls.every(([, init]) => init?.method === undefined || init.method === 'GET')).toBe(true);
    expect(answer.stops[0].stop_level).toBe(45);
  });
});
