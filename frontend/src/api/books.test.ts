/** Endpoint-shape tests for booksApi: URL, method and body. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { booksApi } from './books';
import { mockApi } from '../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('booksApi', () => {
  it('lists, creates and archives through /api/books', async () => {
    const api = mockApi([[/\/api\/books/, { books: [] }]]);
    await booksApi.list();
    await booksApi.create({ name: 'Momentum', starting_cash: 100 });
    await booksApi.archive(7);

    expect(api.calls.map(([url, init]) => [url, init?.method ?? 'GET'])).toEqual([
      ['/api/books', 'GET'],
      ['/api/books', 'POST'],
      ['/api/books/7/archive', 'POST'],
    ]);
    expect(JSON.parse(String(api.calls[1][1]?.body))).toEqual({ name: 'Momentum', starting_cash: 100 });
  });
});
