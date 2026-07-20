/**
 * /api/keyproxy client (BYOK 5a) — pubkey discovery + the Settings-flow key
 * validation relay.
 *
 * The publickey response carries the proxy's envelope-encryption keys
 * (newest first) plus the caller's `sub` — the AAD binding value the browser
 * cannot otherwise learn (Express injects the JWT server-side). It is cached
 * for 10 minutes; envelope encryption re-verifies the SPKI pin on every use,
 * so a stale cache can never widen trust.
 *
 * Never-log policy: envelopes are opaque ciphertext and nothing here logs.
 */
import { apiRequest } from './client';
import type { Envelope } from '../vault/envelope';

/** One proxy envelope-encryption key, as served by GET /api/keyproxy/publickey. */
export interface KeyProxyPublicKey {
  kid: string;
  alg: string;
  /** b64url SPKI DER. */
  spki: string;
}

export interface KeyProxyInfo {
  /** Newest first — encrypt to keys[0]. */
  keys: KeyProxyPublicKey[];
  /** The caller's JWT subject; envelopes must set aad.sub to exactly this. */
  sub: string;
}

/** Scope v1 wire shape (semantics live in the keyproxy — see the plan). */
export interface KeyScope {
  v: number;
  provider: string;
  action: string;
  params: Record<string, unknown>;
  budget: Record<string, number>;
}

export interface KeyValidationResult {
  valid: boolean;
  provider: string;
  key_hint: string;
}

export const KEYPROXY_INFO_CACHE_MS = 10 * 60 * 1000;

let cachedInfo: { info: KeyProxyInfo; fetchedAt: number } | null = null;

/** Pubkey + sub, cached 10 minutes (per the plan's packet 5a contract). */
export async function getKeyProxyInfo(): Promise<KeyProxyInfo> {
  if (cachedInfo !== null && Date.now() - cachedInfo.fetchedAt < KEYPROXY_INFO_CACHE_MS) {
    return cachedInfo.info;
  }
  const info = await apiRequest<KeyProxyInfo>('/api/keyproxy/publickey');
  cachedInfo = { info, fetchedAt: Date.now() };
  return info;
}

export function clearKeyProxyInfoCache(): void {
  cachedInfo = null;
}

/** Relay a sealed key + scope to POST /api/keyproxy/validate. */
export function validateKey(envelope: Envelope, scope: KeyScope): Promise<KeyValidationResult> {
  return apiRequest<KeyValidationResult>('/api/keyproxy/validate', {
    method: 'POST',
    body: JSON.stringify({ envelope, scope }),
  });
}
