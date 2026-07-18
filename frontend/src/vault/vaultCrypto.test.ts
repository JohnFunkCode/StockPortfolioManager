/**
 * Vault passphrase crypto tests (Phase 4).
 *
 * Round-trips use a lowered iteration count (the KDF parameters ride in the
 * record, so unwrap honors whatever wrap wrote) — one test pins the
 * production default at 600k. Never-log policy: failure messages must not
 * carry the API key or passphrase.
 */
import { describe, expect, it } from 'vitest';

import {
  VAULT_IV_LENGTH,
  VAULT_KDF_ALG,
  VAULT_PBKDF2_ITERATIONS,
  VaultCryptoError,
  WRONG_PASSPHRASE_MESSAGE,
  unwrapKey,
  wrapKey,
} from './vaultCrypto';
import { b64urlDecode, b64urlEncode } from './envelope';

const API_KEY = 'sk-ant-test-key-abcd';
const PASSPHRASE = 'correct horse battery staple';
// Fast KDF for tests; production default is pinned separately below.
const TEST_ITERATIONS = 1_000;

describe('vaultCrypto', () => {
  it('pins the production KDF parameters', async () => {
    expect(VAULT_PBKDF2_ITERATIONS).toBe(600_000);
    const wrapped = await wrapKey(API_KEY, PASSPHRASE); // default iterations
    expect(wrapped.kdf).toEqual({ alg: VAULT_KDF_ALG, iterations: 600_000 });
  });

  it('round-trips an API key under the right passphrase', async () => {
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    expect(wrapped.kdf.iterations).toBe(TEST_ITERATIONS);
    expect(b64urlDecode(wrapped.iv)).toHaveLength(VAULT_IV_LENGTH);
    await expect(unwrapKey(wrapped, PASSPHRASE)).resolves.toBe(API_KEY);
  });

  it('uses a fresh salt and iv per wrap', async () => {
    const first = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    const second = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    expect(first.salt).not.toBe(second.salt);
    expect(first.iv).not.toBe(second.iv);
    expect(first.ct).not.toBe(second.ct);
  });

  it('rejects the wrong passphrase with the constant safe message', async () => {
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    const error = await unwrapKey(wrapped, 'not the passphrase').then(
      () => null,
      (exc: unknown) => exc as VaultCryptoError,
    );
    expect(error).toBeInstanceOf(VaultCryptoError);
    expect(error!.message).toBe(WRONG_PASSPHRASE_MESSAGE);
    // Never-log policy: the failure names no secret material.
    expect(error!.message).not.toContain(API_KEY);
    expect(error!.message).not.toContain(PASSPHRASE);
  });

  it('rejects tampered ciphertext identically to a wrong passphrase', async () => {
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    const ct = b64urlDecode(wrapped.ct);
    ct[0] ^= 0xff;
    const tampered = { ...wrapped, ct: b64urlEncode(ct) };
    await expect(unwrapKey(tampered, PASSPHRASE)).rejects.toThrow(WRONG_PASSPHRASE_MESSAGE);
  });

  it('rejects an unsupported KDF fail-closed', async () => {
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    const downgraded = { ...wrapped, kdf: { alg: 'PBKDF1' as never, iterations: 1 } };
    await expect(unwrapKey(downgraded, PASSPHRASE)).rejects.toThrow(
      'vault record has an unsupported KDF',
    );
  });

  it('rejects a corrupted iteration count', async () => {
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    const corrupted = { ...wrapped, kdf: { alg: VAULT_KDF_ALG, iterations: 0 } as const };
    await expect(unwrapKey(corrupted, PASSPHRASE)).rejects.toThrow(
      'vault record has an invalid KDF iteration count',
    );
  });

  it('rejects non-b64url record fields', async () => {
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    await expect(unwrapKey({ ...wrapped, salt: '!!!' }, PASSPHRASE)).rejects.toThrow(
      'vault record salt is not valid b64url',
    );
  });

  it('rejects empty inputs', async () => {
    await expect(wrapKey('', PASSPHRASE)).rejects.toThrow(VaultCryptoError);
    await expect(wrapKey(API_KEY, '')).rejects.toThrow(VaultCryptoError);
    const wrapped = await wrapKey(API_KEY, PASSPHRASE, TEST_ITERATIONS);
    await expect(unwrapKey(wrapped, '')).rejects.toThrow(VaultCryptoError);
  });
});
