/**
 * /api/keyproxy client tests (BYOK 5a): the 10-minute pubkey cache, the
 * validate POST wire shape, and FastAPI `{detail}` errors surfacing as the
 * ApiError message (the copy the dialogs show verbatim).
 *
 * Runs the real api client against a stubbed global fetch so the whole
 * request path — URL, method, body, error extraction — is under test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './client';
import {
  KEYPROXY_INFO_CACHE_MS,
  clearKeyProxyInfoCache,
  getKeyProxyInfo,
  validateKey,
  type KeyProxyInfo,
} from './keyproxy';
import type { Envelope } from '../vault/envelope';

const INFO: KeyProxyInfo = {
  keys: [{ kid: 'kp-2026-07', alg: 'ECDH-ES-P256+HKDF-SHA256+A256GCM', spki: 'AAAA' }],
  sub: 'john',
};

const ENVELOPE: Envelope = {
  v: 1,
  alg: 'ECDH-ES-P256+HKDF-SHA256+A256GCM',
  kid: 'kp-2026-07',
  epk: 'BBBB',
  iv: 'CCCC',
  ct: 'DDDD',
  aad: { sub: 'john', provider: 'anthropic', iat: 1, jti: 'x', scope_hash: 'h' },
};

const SCOPE = {
  v: 1,
  provider: 'anthropic',
  action: 'key.validate',
  params: {},
  budget: { max_calls: 1, max_mutations: 0, ttl: 60 },
};

/** A fresh Response per call — bodies are single-use. */
function respondWith(body: unknown, status = 200): () => Promise<Response> {
  return () =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
}

describe('keyproxy api client', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    clearKeyProxyInfoCache();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('fetches pubkey info once and serves the cache within 10 minutes', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    fetchMock.mockImplementation(respondWith(INFO));

    const first = await getKeyProxyInfo();
    expect(first).toEqual(INFO);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/keyproxy/publickey');

    vi.advanceTimersByTime(KEYPROXY_INFO_CACHE_MS - 1);
    expect(await getKeyProxyInfo()).toEqual(INFO);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('refetches after the cache expires', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    fetchMock.mockImplementation(respondWith(INFO));

    await getKeyProxyInfo();
    vi.advanceTimersByTime(KEYPROXY_INFO_CACHE_MS);
    await getKeyProxyInfo();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('clearKeyProxyInfoCache forces a refetch', async () => {
    fetchMock.mockImplementation(respondWith(INFO));
    await getKeyProxyInfo();
    clearKeyProxyInfoCache();
    await getKeyProxyInfo();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('validateKey POSTs the envelope + scope and returns the result', async () => {
    fetchMock.mockImplementation(respondWith({ valid: true, provider: 'anthropic', key_hint: '…abcd' }));

    const result = await validateKey(ENVELOPE, SCOPE);
    expect(result).toEqual({ valid: true, provider: 'anthropic', key_hint: '…abcd' });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/keyproxy/validate');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ envelope: ENVELOPE, scope: SCOPE });
  });

  it("surfaces FastAPI's {detail} error copy as the ApiError message", async () => {
    fetchMock.mockImplementation(
      respondWith({ detail: 'The key proxy is unavailable; try again shortly.' }, 503),
    );

    await expect(validateKey(ENVELOPE, SCOPE)).rejects.toThrow(
      'The key proxy is unavailable; try again shortly.',
    );
    fetchMock.mockImplementation(respondWith({ detail: 'nope' }, 400));
    const err = await validateKey(ENVELOPE, SCOPE).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
  });
});
