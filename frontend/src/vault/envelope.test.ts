/**
 * BYOK envelope crypto v1 — TypeScript side of the cross-runtime contract.
 *
 * Driven by tests/vectors/keyproxy_envelope_v1.json (shared with
 * test_keyproxy_crypto.py): re-encrypting with each vector's pinned
 * ephemeral key + IV must reproduce the pinned envelope byte-exactly,
 * proving TS-encrypt → Python-decrypt without any cross-language plumbing.
 * All API keys in the vectors are fake strings, never real secrets.
 */
import { describe, expect, it, vi } from 'vitest';

import vectorsRaw from '../../../tests/vectors/keyproxy_envelope_v1.json?raw';

import {
  ENVELOPE_ALG,
  ENVELOPE_VERSION,
  EnvelopeError,
  HKDF_INFO_PREFIX,
  IV_LENGTH,
  UNCOMPRESSED_POINT_LENGTH,
  b64urlDecode,
  b64urlEncode,
  canonicalJson,
  computeScopeHash,
  encryptEnvelope,
  importPinnedPublicKey,
  pinnedFingerprints,
  spkiFingerprint,
  type Envelope,
  type EnvelopeAad,
} from './envelope';

interface CanonicalizationVector {
  name: string;
  input: unknown;
  canonical: string;
  scope_hash: string;
}

interface EnvelopeVector {
  name: string;
  api_key: string;
  scope: unknown;
  ephemeral_private_jwk: JsonWebKey;
  iv: string;
  aad: EnvelopeAad;
  envelope: Envelope;
}

interface Vectors {
  alg: string;
  hkdf_info_prefix: string;
  recipient: {
    kid: string;
    private_jwk: JsonWebKey;
    public_jwk: JsonWebKey;
    spki_fingerprint: string;
  };
  canonicalization: CanonicalizationVector[];
  envelopes: EnvelopeVector[];
}

const vectors = JSON.parse(vectorsRaw) as Vectors;

const EC_PARAMS: EcKeyImportParams = { name: 'ECDH', namedCurve: 'P-256' };
const encoder = new TextEncoder();

async function importRecipientPublicJwk(): Promise<CryptoKey> {
  return crypto.subtle.importKey('jwk', vectors.recipient.public_jwk, EC_PARAMS, true, []);
}

async function recipientSpki(): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.exportKey('spki', await importRecipientPublicJwk()));
}

/**
 * Test-only decrypt (mirrors keyproxy/crypto.py) so the random-ephemeral
 * path can be proven round-trippable inside this suite. Production decrypt
 * lives exclusively in the Key Proxy.
 */
async function decryptWithPrivateJwk(privateJwk: JsonWebKey, envelope: Envelope): Promise<string> {
  const privateKey = await crypto.subtle.importKey('jwk', privateJwk, EC_PARAMS, false, [
    'deriveBits',
  ]);
  const epkBytes = b64urlDecode(envelope.epk);
  const epk = await crypto.subtle.importKey('raw', epkBytes as BufferSource, EC_PARAMS, false, []);
  const sharedSecret = await crypto.subtle.deriveBits({ name: 'ECDH', public: epk }, privateKey, 256);
  const hkdfKey = await crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveBits']);
  const aesKeyBits = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: new Uint8Array(0),
      info: encoder.encode(HKDF_INFO_PREFIX + envelope.kid) as BufferSource,
    },
    hkdfKey,
    256,
  );
  const aesKey = await crypto.subtle.importKey('raw', aesKeyBits, { name: 'AES-GCM' }, false, [
    'decrypt',
  ]);
  const plaintext = await crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv: b64urlDecode(envelope.iv) as BufferSource,
      additionalData: encoder.encode(canonicalJson(envelope.aad)) as BufferSource,
    },
    aesKey,
    b64urlDecode(envelope.ct) as BufferSource,
  );
  return new TextDecoder().decode(plaintext);
}

describe('canonicalJson', () => {
  it.each(vectors.canonicalization)('matches the pinned vector: $name', async (vector) => {
    expect(canonicalJson(vector.input)).toBe(vector.canonical);
    expect(await computeScopeHash(vector.input)).toBe(vector.scope_hash);
  });

  it('is independent of key insertion order', () => {
    expect(canonicalJson({ b: 2, a: 1 })).toBe(canonicalJson({ a: 1, b: 2 }));
  });

  it('rejects non-integer numbers fail-closed', () => {
    for (const bad of [1.5, 0.1, NaN, Infinity, -Infinity, 2 ** 53]) {
      expect(() => canonicalJson({ n: bad })).toThrow(EnvelopeError);
    }
  });

  it('rejects non-ASCII object keys fail-closed', () => {
    expect(() => canonicalJson({ café: 1 })).toThrow(EnvelopeError);
    expect(() => canonicalJson({ outer: { '💹': 1 } })).toThrow(EnvelopeError);
  });

  it('rejects non-JSON types fail-closed', () => {
    expect(() => canonicalJson(undefined)).toThrow(EnvelopeError);
    expect(() => canonicalJson({ v: undefined })).toThrow(EnvelopeError);
    expect(() => canonicalJson({ v: 1n })).toThrow(EnvelopeError);
    expect(() => canonicalJson({ v: () => 1 })).toThrow(EnvelopeError);
    expect(() => canonicalJson({ v: new Date(0) })).toThrow(EnvelopeError);
    expect(() => canonicalJson(new Map())).toThrow(EnvelopeError);
  });

  it('rejects lone surrogates (which cannot UTF-8 encode on the Python side)', () => {
    expect(() => canonicalJson({ v: '\ud800' })).toThrow(EnvelopeError);
    expect(() => canonicalJson({ v: 'tail\udc00' })).toThrow(EnvelopeError);
  });
});

describe('b64url', () => {
  it('round-trips arbitrary bytes', () => {
    const bytes = crypto.getRandomValues(new Uint8Array(41));
    expect(b64urlDecode(b64urlEncode(bytes))).toEqual(bytes);
    expect(b64urlEncode(new Uint8Array(0))).toBe('');
    expect(b64urlDecode('')).toEqual(new Uint8Array(0));
  });

  it('decodes the vector epk to a 65-byte uncompressed point', () => {
    const epk = b64urlDecode(vectors.envelopes[0].envelope.epk);
    expect(epk.length).toBe(UNCOMPRESSED_POINT_LENGTH);
    expect(epk[0]).toBe(0x04);
  });

  it('rejects padding, standard-alphabet characters, and bad lengths', () => {
    for (const bad of ['AA==', 'A+', 'A/', 'A', '!!!']) {
      expect(() => b64urlDecode(bad)).toThrow(EnvelopeError);
    }
  });
});

describe('SPKI pinning', () => {
  it('fingerprints the vector recipient key to the pinned value', async () => {
    expect(await spkiFingerprint(await recipientSpki())).toBe(vectors.recipient.spki_fingerprint);
  });

  it('parses the comma-separated pin list', () => {
    expect(pinnedFingerprints(' a , b ,, ')).toEqual(['a', 'b']);
    expect(pinnedFingerprints('')).toEqual([]);
    expect(pinnedFingerprints(undefined)).toEqual([]);
  });

  it('imports a pinned public key and refuses an unpinned one', async () => {
    const spki = await recipientSpki();
    const key = await importPinnedPublicKey(spki, [vectors.recipient.spki_fingerprint]);
    expect(key.type).toBe('public');
    await expect(importPinnedPublicKey(spki, ['not-the-fingerprint'])).rejects.toThrow(/pin/);
    await expect(importPinnedPublicKey(spki, [])).rejects.toThrow(EnvelopeError);
  });

  it('reads pins from VITE_KEYPROXY_SPKI_PINS by default', async () => {
    const spki = await recipientSpki();
    vi.stubEnv('VITE_KEYPROXY_SPKI_PINS', `other-pin, ${vectors.recipient.spki_fingerprint}`);
    try {
      const key = await importPinnedPublicKey(spki);
      expect(key.type).toBe('public');
    } finally {
      vi.unstubAllEnvs();
    }
    // With no env pins configured, everything is refused.
    await expect(importPinnedPublicKey(spki)).rejects.toThrow(/pin/);
  });
});

describe('envelope vectors (the TS-encrypt → Py-decrypt proof)', () => {
  it.each(vectors.envelopes)(
    'reproduces the pinned envelope byte-exactly: $name',
    async (vector) => {
      expect(await computeScopeHash(vector.scope)).toBe(vector.aad.scope_hash);

      const recipientKey = await importRecipientPublicJwk();
      const envelope = await encryptEnvelope(vector.api_key, recipientKey, {
        kid: vectors.recipient.kid,
        aad: vector.aad,
        pins: [vectors.recipient.spki_fingerprint],
        ephemeralPrivateJwk: vector.ephemeral_private_jwk,
        iv: b64urlDecode(vector.iv),
      });
      expect(envelope).toEqual(vector.envelope);
    },
  );
});

describe('encryptEnvelope', () => {
  const aad: EnvelopeAad = vectors.envelopes[0].aad;

  it('refuses to encrypt to an unpinned recipient key, even a CryptoKey handed in directly', async () => {
    const recipientKey = await importRecipientPublicJwk();
    const attempt = (pins: string[] | undefined) =>
      encryptEnvelope('sk-ant-api03-TEST-unpinned', recipientKey, {
        kid: vectors.recipient.kid,
        aad,
        pins,
      });
    await expect(attempt([])).rejects.toThrow(/pin/);
    await expect(attempt(['not-the-fingerprint'])).rejects.toThrow(/pin/);
    // No pins option and no VITE_KEYPROXY_SPKI_PINS → refused.
    await expect(attempt(undefined)).rejects.toThrow(/pin/);
    // Errors never carry the API key.
    const error = await attempt([]).catch((e: unknown) => e);
    expect(String(error)).not.toContain('sk-ant');
  });

  it('produces a well-formed envelope on the random-ephemeral path, decryptable by the recipient', async () => {
    const apiKey = 'sk-ant-api03-TEST-VECTOR-random-path-0000000000000000';
    const recipientKey = await importRecipientPublicJwk();
    const options = {
      kid: vectors.recipient.kid,
      aad,
      pins: [vectors.recipient.spki_fingerprint],
    };
    const envelope = await encryptEnvelope(apiKey, recipientKey, options);

    expect(envelope.v).toBe(ENVELOPE_VERSION);
    expect(envelope.alg).toBe(ENVELOPE_ALG);
    expect(envelope.kid).toBe(vectors.recipient.kid);
    const epk = b64urlDecode(envelope.epk);
    expect(epk.length).toBe(UNCOMPRESSED_POINT_LENGTH);
    expect(epk[0]).toBe(0x04);
    expect(b64urlDecode(envelope.iv).length).toBe(IV_LENGTH);
    expect(b64urlDecode(envelope.ct).length).toBe(encoder.encode(apiKey).length + 16);
    expect(envelope.aad).toEqual(aad);
    expect(envelope.aad).not.toBe(aad);

    // Fresh ephemeral + IV every call — no reuse across envelopes.
    const second = await encryptEnvelope(apiKey, recipientKey, options);
    expect(second.epk).not.toBe(envelope.epk);
    expect(second.iv).not.toBe(envelope.iv);
    expect(second.ct).not.toBe(envelope.ct);

    expect(await decryptWithPrivateJwk(vectors.recipient.private_jwk, envelope)).toBe(apiKey);
  });

  it('rejects malformed aad fail-closed', async () => {
    const recipientKey = await importRecipientPublicJwk();
    const attempt = (badAad: unknown) =>
      encryptEnvelope('sk-ant-api03-TEST-aad', recipientKey, {
        kid: vectors.recipient.kid,
        aad: badAad as EnvelopeAad,
        pins: [vectors.recipient.spki_fingerprint],
      });
    const { scope_hash: _dropped, ...missingKey } = aad;
    await expect(attempt(missingKey)).rejects.toThrow(EnvelopeError);
    await expect(attempt({ ...aad, extra: 'x' })).rejects.toThrow(EnvelopeError);
    await expect(attempt({ ...aad, iat: 1.5 })).rejects.toThrow(EnvelopeError);
    await expect(attempt({ ...aad, iat: '1752570000' })).rejects.toThrow(EnvelopeError);
    await expect(attempt({ ...aad, iat: true })).rejects.toThrow(EnvelopeError);
    await expect(attempt({ ...aad, sub: 42 })).rejects.toThrow(EnvelopeError);
    await expect(attempt({ ...aad, jti: null })).rejects.toThrow(EnvelopeError);
    await expect(attempt(null)).rejects.toThrow(EnvelopeError);
    await expect(attempt([aad])).rejects.toThrow(EnvelopeError);
  });

  it('rejects an empty api key, empty kid, and wrong-length iv', async () => {
    const recipientKey = await importRecipientPublicJwk();
    const options = { kid: vectors.recipient.kid, aad, pins: [vectors.recipient.spki_fingerprint] };
    await expect(encryptEnvelope('', recipientKey, options)).rejects.toThrow(EnvelopeError);
    await expect(
      encryptEnvelope('sk-ant-api03-TEST', recipientKey, { ...options, kid: '' }),
    ).rejects.toThrow(EnvelopeError);
    await expect(
      encryptEnvelope('sk-ant-api03-TEST', recipientKey, { ...options, iv: new Uint8Array(11) }),
    ).rejects.toThrow(EnvelopeError);
  });
});
