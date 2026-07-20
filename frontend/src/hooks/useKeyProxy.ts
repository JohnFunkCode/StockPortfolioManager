/**
 * Key-proxy hooks (BYOK 5a): cached pubkey info + the Settings validation
 * flow — seal the pasted key to the pinned proxy public key, relay it through
 * POST /api/keyproxy/validate, and surface the result.
 *
 * The plaintext key exists here only as a function argument on its way into
 * WebCrypto — never state, never storage, never logged. Envelope encryption
 * refuses any proxy key whose SPKI fingerprint is not in the build-time pin
 * list (VITE_KEYPROXY_SPKI_PINS).
 */
import { useMutation, useQuery } from '@tanstack/react-query';

import {
  KEYPROXY_INFO_CACHE_MS,
  getKeyProxyInfo,
  validateKey,
  type KeyScope,
  type KeyValidationResult,
} from '../api/keyproxy';
import {
  b64urlDecode,
  computeScopeHash,
  encryptEnvelope,
  importPinnedPublicKey,
} from '../vault/envelope';

export function useKeyProxyInfo() {
  return useQuery({
    queryKey: ['keyproxy-info'],
    queryFn: getKeyProxyInfo,
    staleTime: KEYPROXY_INFO_CACHE_MS,
  });
}

/** Scope for a Settings-flow validation: exactly one read call, short TTL. */
export function keyValidationScope(provider: string): KeyScope {
  return {
    v: 1,
    provider,
    action: 'key.validate',
    params: {},
    budget: { max_calls: 1, max_mutations: 0, ttl: 60 },
  };
}

/**
 * Scope for one chat turn: ambient tier — read-only tool calls allowed, zero
 * mutations, a hard token ceiling, and a TTL covering a single streamed turn.
 */
export function chatTurnScope(provider: string): KeyScope {
  return {
    v: 1,
    provider,
    action: 'chat.turn',
    params: {},
    budget: { max_calls: 20, max_mutations: 0, max_tokens: 250000, ttl: 300 },
  };
}

/**
 * Seal `apiKey` under `scope` into a single-use envelope bound to the
 * caller's `sub` and the pinned proxy public key. Fresh jti/iat per call —
 * every envelope is one-shot by construction (replay cache rejects reuse).
 */
async function sealKey(provider: string, apiKey: string, scope: KeyScope) {
  const info = await getKeyProxyInfo();
  const [newest] = info.keys;
  if (newest === undefined) {
    throw new Error('The key proxy returned no public keys; try again later.');
  }
  const recipientKey = await importPinnedPublicKey(b64urlDecode(newest.spki));
  const envelope = await encryptEnvelope(apiKey, recipientKey, {
    kid: newest.kid,
    aad: {
      sub: info.sub,
      provider,
      iat: Math.floor(Date.now() / 1000),
      jti: crypto.randomUUID(),
      scope_hash: await computeScopeHash(scope),
    },
  });
  return { envelope, scope };
}

/**
 * Seal `apiKey` for a single chat turn. Called fresh on every send — one
 * user action, one envelope.
 */
export async function sealKeyForTurn(provider: string, apiKey: string) {
  return sealKey(provider, apiKey, chatTurnScope(provider));
}

/**
 * Seal `apiKey` into a single-use envelope bound to the caller's `sub` and a
 * `key.validate` scope, then relay it for validation. Throws with safe,
 * user-facing copy on every failure path.
 */
export async function sealAndValidateKey(
  provider: string,
  apiKey: string,
): Promise<KeyValidationResult> {
  const { envelope, scope } = await sealKey(provider, apiKey, keyValidationScope(provider));
  const result = await validateKey(envelope, scope);
  if (!result.valid) {
    throw new Error('The provider rejected this key. Check that you pasted it completely.');
  }
  return result;
}

/** Mutation wrapper for the add/rotate dialogs. */
export function useValidateKey() {
  return useMutation({
    mutationFn: ({ provider, apiKey }: { provider: string; apiKey: string }) =>
      sealAndValidateKey(provider, apiKey),
  });
}
