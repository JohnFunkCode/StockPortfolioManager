/**
 * BYOK envelope crypto v1 — the browser (encrypt) half of the cross-runtime
 * contract in docs/proposals/byok-key-proxy-plan.md ("Envelope spec (v1)").
 *
 * The decrypt half lives in keyproxy/crypto.py; both sides are pinned
 * byte-exact by tests/vectors/keyproxy_envelope_v1.json. Anything that could
 * make the two runtimes serialize the same value differently is rejected
 * fail-closed rather than papered over:
 *
 *  - object keys must be ASCII (JS sorts keys by UTF-16 code unit, Python by
 *    code point — they diverge above the BMP);
 *  - numbers must be integers within the JS safe range (float formatting
 *    diverges between the runtimes);
 *  - strings must be well-formed Unicode (lone surrogates cannot UTF-8
 *    encode on the Python side).
 *
 * Pure crypto.subtle — no dependencies. ECDH P-256 → HKDF-SHA256 (empty
 * salt, per RFC 5869 equal to HashLen zero bytes — WebCrypto's empty
 * ArrayBuffer matches Python cryptography's salt=None) → AES-256-GCM, with
 * the canonical AAD JSON as GCM additional data.
 *
 * SPKI pinning: the proxy public key reaches the browser through
 * quantcore-api, so encryptEnvelope refuses any recipient key whose SPKI
 * fingerprint is not in the build-time pin list (VITE_KEYPROXY_SPKI_PINS) —
 * a substituted key would otherwise silently collapse the "application sees
 * ciphertext only" boundary.
 *
 * Never-log policy: no error thrown here may carry the API key, envelope
 * contents, or key material — messages describe structure only.
 */

export const ENVELOPE_VERSION = 1;
export const ENVELOPE_ALG = 'ECDH-ES-P256+HKDF-SHA256+A256GCM';
export const HKDF_INFO_PREFIX = 'quantcore-keyproxy-v1|';
export const IV_LENGTH = 12;
export const UNCOMPRESSED_POINT_LENGTH = 65;

const EC_PARAMS: EcKeyImportParams = { name: 'ECDH', namedCurve: 'P-256' };
const COORDINATE_LENGTH = 32;

/** Thrown for every contract violation; never carries key material. */
export class EnvelopeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EnvelopeError';
  }
}

export interface EnvelopeAad {
  sub: string;
  provider: string;
  iat: number;
  jti: string;
  scope_hash: string;
}

export interface Envelope {
  v: number;
  alg: string;
  kid: string;
  epk: string;
  iv: string;
  ct: string;
  aad: EnvelopeAad;
}

const encoder = new TextEncoder();

// ---------------------------------------------------------------------------
// b64url (unpadded, strict)
// ---------------------------------------------------------------------------

export function b64urlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64urlDecode(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]*$/.test(value)) {
    throw new EnvelopeError('b64url value contains invalid characters');
  }
  if (value.length % 4 === 1) {
    throw new EnvelopeError('b64url value has invalid length');
  }
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4);
  let binary: string;
  try {
    binary = atob(padded);
  } catch {
    throw new EnvelopeError('b64url value is not decodable');
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ---------------------------------------------------------------------------
// Canonical JSON — must produce byte-identical output to
// keyproxy/crypto.py's canonical_json() for every accepted value.
// ---------------------------------------------------------------------------

function isWellFormedString(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const unit = value.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(i + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        return false;
      }
      i++;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function serializeCanonical(value: unknown, out: string[]): void {
  if (value === null) {
    out.push('null');
    return;
  }
  switch (typeof value) {
    case 'boolean':
      out.push(value ? 'true' : 'false');
      return;
    case 'number':
      if (!Number.isSafeInteger(value)) {
        throw new EnvelopeError('canonical JSON numbers must be integers within the safe range');
      }
      out.push(JSON.stringify(value));
      return;
    case 'string':
      if (!isWellFormedString(value)) {
        throw new EnvelopeError('canonical JSON strings must be well-formed Unicode');
      }
      out.push(JSON.stringify(value));
      return;
    case 'object': {
      if (Array.isArray(value)) {
        out.push('[');
        value.forEach((item, index) => {
          if (index > 0) out.push(',');
          serializeCanonical(item, out);
        });
        out.push(']');
        return;
      }
      const proto = Object.getPrototypeOf(value);
      if (proto !== Object.prototype && proto !== null) {
        throw new EnvelopeError('canonical JSON objects must be plain objects');
      }
      const keys = Object.keys(value).sort();
      out.push('{');
      keys.forEach((key, index) => {
        // eslint-disable-next-line no-control-regex
        if (!/^[\x00-\x7F]*$/.test(key)) {
          throw new EnvelopeError('canonical JSON object keys must be ASCII');
        }
        if (index > 0) out.push(',');
        out.push(JSON.stringify(key), ':');
        serializeCanonical((value as Record<string, unknown>)[key], out);
      });
      out.push('}');
      return;
    }
    default:
      throw new EnvelopeError(`canonical JSON cannot contain a ${typeof value}`);
  }
}

export function canonicalJson(value: unknown): string {
  const out: string[] = [];
  serializeCanonical(value, out);
  return out.join('');
}

async function sha256(bytes: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource));
}

/** b64url SHA-256 of the scope's canonical JSON — the aad.scope_hash value. */
export async function computeScopeHash(scope: unknown): Promise<string> {
  return b64urlEncode(await sha256(encoder.encode(canonicalJson(scope))));
}

// ---------------------------------------------------------------------------
// SPKI pinning
// ---------------------------------------------------------------------------

/** b64url SHA-256 of a public key's SPKI DER — the VITE_KEYPROXY_SPKI_PINS format. */
export async function spkiFingerprint(spkiDer: Uint8Array): Promise<string> {
  return b64urlEncode(await sha256(spkiDer));
}

/** Parse the comma-separated build-time pin list (default: VITE_KEYPROXY_SPKI_PINS). */
export function pinnedFingerprints(
  raw: string | undefined = import.meta.env.VITE_KEYPROXY_SPKI_PINS as string | undefined,
): string[] {
  return (raw ?? '')
    .split(',')
    .map((pin) => pin.trim())
    .filter((pin) => pin.length > 0);
}

/**
 * Import the Key Proxy public key (SPKI DER, as served by /v1/publickey),
 * refusing any key whose fingerprint is not pinned. Imported extractable so
 * encryptEnvelope can independently re-verify the pin.
 */
export async function importPinnedPublicKey(
  spkiDer: Uint8Array,
  pins: string[] = pinnedFingerprints(),
): Promise<CryptoKey> {
  const fingerprint = await spkiFingerprint(spkiDer);
  if (!pins.includes(fingerprint)) {
    throw new EnvelopeError('recipient public key is not in the SPKI pin list; refusing to encrypt');
  }
  try {
    return await crypto.subtle.importKey('spki', spkiDer as BufferSource, EC_PARAMS, true, []);
  } catch {
    throw new EnvelopeError('recipient public key is not a valid P-256 SPKI key');
  }
}

// ---------------------------------------------------------------------------
// Envelope encryption
// ---------------------------------------------------------------------------

const AAD_KEYS = ['iat', 'jti', 'provider', 'scope_hash', 'sub'] as const;

function validateAad(aad: EnvelopeAad): void {
  if (typeof aad !== 'object' || aad === null || Array.isArray(aad)) {
    throw new EnvelopeError('aad must be an object');
  }
  const keys = Object.keys(aad).sort();
  if (keys.length !== AAD_KEYS.length || keys.some((key, i) => key !== AAD_KEYS[i])) {
    throw new EnvelopeError('aad must contain exactly sub, provider, iat, jti, scope_hash');
  }
  for (const field of ['sub', 'provider', 'jti', 'scope_hash'] as const) {
    if (typeof aad[field] !== 'string') {
      throw new EnvelopeError(`aad.${field} must be a string`);
    }
  }
  if (typeof aad.iat !== 'number' || !Number.isSafeInteger(aad.iat)) {
    throw new EnvelopeError('aad.iat must be an integer');
  }
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function jwkCoordinate(jwk: JsonWebKey, field: 'x' | 'y'): Uint8Array {
  const value = jwk[field];
  if (typeof value !== 'string') {
    throw new EnvelopeError(`ephemeral JWK is missing the ${field} coordinate`);
  }
  const bytes = b64urlDecode(value);
  if (bytes.length !== COORDINATE_LENGTH) {
    throw new EnvelopeError(`ephemeral JWK ${field} coordinate has the wrong length`);
  }
  return bytes;
}

export interface EncryptEnvelopeOptions {
  kid: string;
  aad: EnvelopeAad;
  /** Overrides the VITE_KEYPROXY_SPKI_PINS pin list (tests). */
  pins?: string[];
  /** Deterministic ephemeral key — TEST VECTORS ONLY, never in production. */
  ephemeralPrivateJwk?: JsonWebKey;
  /** Deterministic IV — TEST VECTORS ONLY, never in production. */
  iv?: Uint8Array;
}

/**
 * Encrypt an API key to the pinned Key Proxy public key, producing an
 * envelope-v1 object that keyproxy/crypto.py's decrypt_envelope accepts.
 */
export async function encryptEnvelope(
  apiKey: string,
  recipientKey: CryptoKey,
  options: EncryptEnvelopeOptions,
): Promise<Envelope> {
  const { kid, aad } = options;
  if (typeof apiKey !== 'string' || apiKey.length === 0) {
    throw new EnvelopeError('apiKey must be a non-empty string');
  }
  if (typeof kid !== 'string' || kid.length === 0) {
    throw new EnvelopeError('kid must be a non-empty string');
  }
  validateAad(aad);

  // Structural pin check: even a CryptoKey handed in directly is verified,
  // so there is no code path that encrypts to an unpinned key.
  const spkiDer = new Uint8Array(await crypto.subtle.exportKey('spki', recipientKey));
  const fingerprint = await spkiFingerprint(spkiDer);
  const pins = options.pins ?? pinnedFingerprints();
  if (!pins.includes(fingerprint)) {
    throw new EnvelopeError('recipient public key is not in the SPKI pin list; refusing to encrypt');
  }

  let ephemeralPrivateKey: CryptoKey;
  let epkBytes: Uint8Array;
  if (options.ephemeralPrivateJwk !== undefined) {
    const jwk = options.ephemeralPrivateJwk;
    try {
      ephemeralPrivateKey = await crypto.subtle.importKey('jwk', jwk, EC_PARAMS, false, ['deriveBits']);
    } catch {
      throw new EnvelopeError('ephemeral JWK is not a valid P-256 private key');
    }
    epkBytes = concatBytes(new Uint8Array([0x04]), jwkCoordinate(jwk, 'x'), jwkCoordinate(jwk, 'y'));
  } else {
    const pair = await crypto.subtle.generateKey(EC_PARAMS, false, ['deriveBits']);
    ephemeralPrivateKey = pair.privateKey;
    epkBytes = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  }
  if (epkBytes.length !== UNCOMPRESSED_POINT_LENGTH || epkBytes[0] !== 0x04) {
    throw new EnvelopeError('ephemeral public key is not an uncompressed P-256 point');
  }

  const iv = options.iv ?? crypto.getRandomValues(new Uint8Array(IV_LENGTH));
  if (iv.length !== IV_LENGTH) {
    throw new EnvelopeError(`iv must be exactly ${IV_LENGTH} bytes`);
  }

  const sharedSecret = await crypto.subtle.deriveBits(
    { name: 'ECDH', public: recipientKey },
    ephemeralPrivateKey,
    256,
  );
  const hkdfKey = await crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveBits']);
  const aesKeyBits = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: new Uint8Array(0),
      info: encoder.encode(HKDF_INFO_PREFIX + kid) as BufferSource,
    },
    hkdfKey,
    256,
  );
  const aesKey = await crypto.subtle.importKey('raw', aesKeyBits, { name: 'AES-GCM' }, false, ['encrypt']);

  const aadBytes = encoder.encode(canonicalJson(aad));
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: iv as BufferSource, additionalData: aadBytes as BufferSource },
      aesKey,
      encoder.encode(apiKey) as BufferSource,
    ),
  );

  return {
    v: ENVELOPE_VERSION,
    alg: ENVELOPE_ALG,
    kid,
    epk: b64urlEncode(epkBytes),
    iv: b64urlEncode(iv),
    ct: b64urlEncode(ciphertext),
    aad: { ...aad },
  };
}
