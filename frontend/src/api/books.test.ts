/** Endpoint-shape tests for booksApi: URL, method and body. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { booksApi, performanceApi } from './books';
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

  it('reads one book\'s performance, and every book\'s for the Compare view', async () => {
    const api = mockApi([[/\/api\/books\/performance/, { books: [] }]]);
    await performanceApi.get('year');
    await performanceApi.get('month', 7);
    await performanceApi.compare('all');
    expect(api.calls.map(([url, init]) => [url, init?.method ?? 'GET'])).toEqual([
      ['/api/books/performance?period=year', 'GET'],
      ['/api/books/performance?period=month&book_id=7', 'GET'],
      ['/api/books/performance/compare?period=all', 'GET'],
    ]);
  });
});
