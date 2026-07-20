/**
 * BYOK browser vault — passphrase wrap/unwrap (Phase 4).
 *
 * PBKDF2-SHA256 (600k iterations, fresh 16-byte salt per wrap) derives an
 * AES-256-GCM key from the vault passphrase; the API key is sealed under a
 * fresh 12-byte IV. The salt/iterations ride in the stored record, so unwrap
 * always honors the parameters the record was written with.
 *
 * The IndexedDB blob is offline-brute-forceable if exfiltrated — the
 * iteration count is the brake, and Phase 5's passphrase-strength minimum is
 * the real control. Never lower VAULT_PBKDF2_ITERATIONS for new wraps.
 *
 * Never-log policy: nothing here logs, and no error may carry the API key,
 * the passphrase, or ciphertext — every failure maps to a constant message.
 */

import { EnvelopeError, b64urlDecode, b64urlEncode } from './envelope';
import type { VaultKdfParams, VaultRecord } from './vaultStore';

export const VAULT_KDF_ALG = 'PBKDF2-SHA256';
export const VAULT_PBKDF2_ITERATIONS = 600_000;
export const VAULT_SALT_LENGTH = 16;
export const VAULT_IV_LENGTH = 12;

/** The one user-facing unwrap failure — wrong passphrase and a corrupted
 * record are deliberately indistinguishable (GCM authentication failure). */
export const WRONG_PASSPHRASE_MESSAGE = 'Incorrect passphrase (or the stored key is corrupted).';

/** Thrown for every wrap/unwrap failure; never carries key material. */
export class VaultCryptoError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'VaultCryptoError';
  }
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

async function deriveAesKey(
  passphrase: string,
  salt: Uint8Array,
  iterations: number,
): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey(
    'raw',
    encoder.encode(passphrase) as BufferSource,
    'PBKDF2',
    false,
    ['deriveKey'],
  );
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt: salt as BufferSource, iterations },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}

/** The encrypted fields of a VaultRecord (metadata is the caller's job). */
export type WrappedKey = Pick<VaultRecord, 'ct' | 'iv' | 'salt' | 'kdf'>;

/**
 * Seal an API key under the vault passphrase. `iterations` is overridable
 * for tests only — production callers must use the default.
 */
export async function wrapKey(
  apiKey: string,
  passphrase: string,
  iterations: number = VAULT_PBKDF2_ITERATIONS,
): Promise<WrappedKey> {
  if (typeof apiKey !== 'string' || apiKey.length === 0) {
    throw new VaultCryptoError('apiKey must be a non-empty string');
  }
  if (typeof passphrase !== 'string' || passphrase.length === 0) {
    throw new VaultCryptoError('passphrase must be a non-empty string');
  }
  if (!Number.isSafeInteger(iterations) || iterations <= 0) {
    throw new VaultCryptoError('iterations must be a positive integer');
  }
  const salt = crypto.getRandomValues(new Uint8Array(VAULT_SALT_LENGTH));
  const iv = crypto.getRandomValues(new Uint8Array(VAULT_IV_LENGTH));
  const aesKey = await deriveAesKey(passphrase, salt, iterations);
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      aesKey,
      encoder.encode(apiKey) as BufferSource,
    ),
  );
  return {
    ct: b64urlEncode(ciphertext),
    iv: b64urlEncode(iv),
    salt: b64urlEncode(salt),
    kdf: { alg: VAULT_KDF_ALG, iterations },
  };
}

function decodeField(record: WrappedKey, field: 'ct' | 'iv' | 'salt'): Uint8Array {
  const value = record[field];
  if (typeof value !== 'string') {
    throw new VaultCryptoError(`vault record ${field} must be a string`);
  }
  try {
    return b64urlDecode(value);
  } catch (exc) {
    if (exc instanceof EnvelopeError) {
      throw new VaultCryptoError(`vault record ${field} is not valid b64url`);
    }
    throw exc;
  }
}

function validateKdf(kdf: VaultKdfParams): void {
  if (typeof kdf !== 'object' || kdf === null || kdf.alg !== VAULT_KDF_ALG) {
    throw new VaultCryptoError('vault record has an unsupported KDF');
  }
  if (!Number.isSafeInteger(kdf.iterations) || kdf.iterations <= 0) {
    throw new VaultCryptoError('vault record has an invalid KDF iteration count');
  }
}

/** Recover the plaintext API key, or throw WRONG_PASSPHRASE_MESSAGE. */
export async function unwrapKey(record: WrappedKey, passphrase: string): Promise<string> {
  if (typeof passphrase !== 'string' || passphrase.length === 0) {
    throw new VaultCryptoError('passphrase must be a non-empty string');
  }
  validateKdf(record.kdf);
  const ct = decodeField(record, 'ct');
  const iv = decodeField(record, 'iv');
  const salt = decodeField(record, 'salt');
  if (iv.length !== VAULT_IV_LENGTH) {
    throw new VaultCryptoError('vault record iv has the wrong length');
  }
  const aesKey = await deriveAesKey(passphrase, salt, record.kdf.iterations);
  let plaintext: ArrayBuffer;
  try {
    plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      aesKey,
      ct as BufferSource,
    );
  } catch {
    throw new VaultCryptoError(WRONG_PASSPHRASE_MESSAGE);
  }
  return decoder.decode(plaintext);
}
