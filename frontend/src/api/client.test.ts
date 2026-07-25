import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiRequest } from './client';

afterEach(() => vi.unstubAllGlobals());

describe('apiRequest', () => {
  it('returns the parsed JSON body on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    ));
    await expect(apiRequest('/api/x')).resolves.toEqual({ ok: true });
  });

  it('throws ApiError with the server message on a JSON error body', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'nope' }), { status: 422 }),
    ));
    await expect(apiRequest('/api/x')).rejects.toMatchObject({
      status: 422,
      message: 'nope',
    });
  });

  it('falls back to statusText when the error body is not JSON', async () => {
    // Non-JSON body -> response.json() rejects -> the .catch(() => ({})) path.
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('<html>Bad Gateway', { status: 502, statusText: 'Bad Gateway' }),
    ));
    await expect(apiRequest('/api/x')).rejects.toBeInstanceOf(ApiError);
    await expect(apiRequest('/api/x')).rejects.toMatchObject({ status: 502 });
  });
});
