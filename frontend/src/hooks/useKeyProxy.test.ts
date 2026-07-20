/**
 * sealAndValidateKey tests (BYOK 5a): real P-256 envelope crypto against a
 * mocked api module. Proves the envelope is bound to the caller's sub and the
 * scope hash, that the plaintext key never appears in what goes on the wire
 * (never-log), and that an unpinned proxy key is refused.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  b64urlEncode,
  computeScopeHash,
  spkiFingerprint,
  type Envelope,
} from '../vault/envelope';
import {
  chatTurnScope,
  keyValidationScope,
  sealAndValidateKey,
  sealKeyForTurn,
} from './useKeyProxy';
import { getKeyProxyInfo, validateKey } from '../api/keyproxy';

vi.mock('../api/keyproxy', async () => {
  const actual = await vi.importActual<typeof import('../api/keyproxy')>('../api/keyproxy');
  return { ...actual, getKeyProxyInfo: vi.fn(), validateKey: vi.fn() };
});

const API_KEY = 'sk-ant-test-secret-key-abcd';

const getInfoMock = vi.mocked(getKeyProxyInfo);
const validateMock = vi.mocked(validateKey);

let spkiB64: string;

async function generateProxyKey(): Promise<{ spkiB64: string; fingerprint: string }> {
  const pair = await crypto.subtle.generateKey(
    { name: 'ECDH', namedCurve: 'P-256' },
    true,
    ['deriveBits'],
  );
  const spkiDer = new Uint8Array(await crypto.subtle.exportKey('spki', pair.publicKey));
  return { spkiB64: b64urlEncode(spkiDer), fingerprint: await spkiFingerprint(spkiDer) };
}

beforeEach(async () => {
  vi.clearAllMocks();
  const proxy = await generateProxyKey();
  spkiB64 = proxy.spkiB64;
  vi.stubEnv('VITE_KEYPROXY_SPKI_PINS', proxy.fingerprint);
  getInfoMock.mockResolvedValue({
    keys: [{ kid: 'kp-test', alg: 'ECDH-ES-P256+HKDF-SHA256+A256GCM', spki: spkiB64 }],
    sub: 'john',
  });
  validateMock.mockResolvedValue({ valid: true, provider: 'anthropic', key_hint: '…abcd' });
});

describe('keyValidationScope', () => {
  it('is a single-call, zero-mutation, short-TTL scope', () => {
    expect(keyValidationScope('anthropic')).toEqual({
      v: 1,
      provider: 'anthropic',
      action: 'key.validate',
      params: {},
      budget: { max_calls: 1, max_mutations: 0, ttl: 60 },
    });
  });
});

describe('chatTurnScope', () => {
  it('is the ambient chat tier: reads only, zero mutations, hard token ceiling', () => {
    expect(chatTurnScope('anthropic')).toEqual({
      v: 1,
      provider: 'anthropic',
      action: 'chat.turn',
      params: {},
      budget: { max_calls: 20, max_mutations: 0, max_tokens: 250000, ttl: 300 },
    });
  });
});

describe('sealKeyForTurn', () => {
  it('seals to the newest pinned key under the chat.turn scope', async () => {
    const { envelope, scope } = await sealKeyForTurn('anthropic', API_KEY);
    expect(scope).toEqual(chatTurnScope('anthropic'));
    expect(envelope.kid).toBe('kp-test');
    expect(envelope.aad.sub).toBe('john');
    expect(envelope.aad.provider).toBe('anthropic');
    expect(envelope.aad.scope_hash).toBe(await computeScopeHash(scope));
    expect(envelope.aad.jti).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('mints a fresh jti per call — one user action, one single-use envelope', async () => {
    const first = await sealKeyForTurn('anthropic', API_KEY);
    const second = await sealKeyForTurn('anthropic', API_KEY);
    expect(first.envelope.aad.jti).not.toBe(second.envelope.aad.jti);
    expect(first.envelope.ct).not.toBe(second.envelope.ct);
  });

  it('never puts the plaintext key in the serialized turn payload (never-log)', async () => {
    const { envelope, scope } = await sealKeyForTurn('anthropic', API_KEY);
    expect(JSON.stringify({ key_envelope: envelope, scope })).not.toContain(API_KEY);
  });

  it('refuses a proxy key whose fingerprint is not pinned', async () => {
    const other = await generateProxyKey();
    vi.stubEnv('VITE_KEYPROXY_SPKI_PINS', other.fingerprint);
    await expect(sealKeyForTurn('anthropic', API_KEY)).rejects.toThrow(
      'recipient public key is not in the SPKI pin list; refusing to encrypt',
    );
  });
});

describe('sealAndValidateKey', () => {
  it('seals to the newest pinned key with sub/provider/scope_hash-bound AAD', async () => {
    const result = await sealAndValidateKey('anthropic', API_KEY);
    expect(result.valid).toBe(true);

    expect(validateMock).toHaveBeenCalledTimes(1);
    const [envelope, scope] = validateMock.mock.calls[0] as [Envelope, unknown];
    expect(scope).toEqual(keyValidationScope('anthropic'));
    expect(envelope.kid).toBe('kp-test');
    expect(envelope.aad.sub).toBe('john');
    expect(envelope.aad.provider).toBe('anthropic');
    expect(envelope.aad.scope_hash).toBe(await computeScopeHash(scope));
    expect(envelope.aad.jti).toMatch(/^[0-9a-f-]{36}$/);
    expect(Math.abs(envelope.aad.iat - Date.now() / 1000)).toBeLessThan(60);
  });

  it('never puts the plaintext key on the wire (never-log)', async () => {
    await sealAndValidateKey('anthropic', API_KEY);
    const [envelope] = validateMock.mock.calls[0];
    expect(JSON.stringify(envelope)).not.toContain(API_KEY);
  });

  it('throws user-facing copy when the provider rejects the key', async () => {
    validateMock.mockResolvedValue({ valid: false, provider: 'anthropic', key_hint: '' });
    await expect(sealAndValidateKey('anthropic', API_KEY)).rejects.toThrow(
      'The provider rejected this key. Check that you pasted it completely.',
    );
  });

  it('throws when the proxy returns no public keys', async () => {
    getInfoMock.mockResolvedValue({ keys: [], sub: 'john' });
    await expect(sealAndValidateKey('anthropic', API_KEY)).rejects.toThrow(
      'The key proxy returned no public keys; try again later.',
    );
    expect(validateMock).not.toHaveBeenCalled();
  });

  it('refuses a proxy key whose fingerprint is not pinned', async () => {
    // Rotate the pin away from the served key: encryption must refuse it.
    const other = await generateProxyKey();
    vi.stubEnv('VITE_KEYPROXY_SPKI_PINS', other.fingerprint);
    await expect(sealAndValidateKey('anthropic', API_KEY)).rejects.toThrow(
      'recipient public key is not in the SPKI pin list; refusing to encrypt',
    );
    expect(validateMock).not.toHaveBeenCalled();
  });
});
