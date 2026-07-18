// QuantUI per-user token mint (BYOK packet 7b, decision #13).
//
// In cloud, IAP has already authenticated the person before a request reaches
// this server; IAP attaches a Google-signed ES256 JWT in the
// `x-goog-iap-jwt-assertion` header. This module verifies that assertion
// against Google's public JWKS (issuer + per-service audience), then mints a
// short-lived ES256 JWT of our own — `sub` = the IAP email, `exp` ≈ 15 min,
// `aud` = both backend services — signed with a private key ONLY this Express
// server holds (`quantui-signing-key` secret). quantcore-api and the keyproxy
// verify with the public half, so verifiers cannot mint identities.
//
// Fallback ladder (keyed on configuration, so a deploy without the new
// secrets keeps today's behavior):
//   1. QUANTUI_SIGNING_KEY set        -> per-user mint; a missing/invalid IAP
//                                        assertion is a hard 401 (never falls
//                                        through to the shared token).
//   2. else QUANTCORE_API_TOKEN set   -> legacy static service token.
//   3. else                           -> no Authorization header (compose,
//                                        where the api runs AUTH_DISABLED).
//
// Never-log policy: assertions, minted tokens, and jose error messages never
// reach a log line or a response body.

import { SignJWT, jwtVerify, importPKCS8, createRemoteJWKSet } from 'jose';

export const IAP_ISSUER = 'https://cloud.google.com/iap';
export const IAP_JWKS_URL = 'https://www.gstatic.com/iap/verify/public_key-jwk';
export const USER_TOKEN_AUDIENCE = ['quantcore-api', 'quantcore-keyproxy'];

const TOKEN_TTL_SECONDS = 15 * 60;
// Re-mint when a cached token has less than this long to live, so a token is
// never handed out about to expire mid-request.
const REMINT_MARGIN_SECONDS = 60;

/** Raised for any assertion problem — the caller maps it to a uniform 401. */
export class IapAuthError extends Error {}

/**
 * Build the auth provider for the /api proxy.
 *
 * @param {object} opts
 * @param {string|null} opts.signingKeyPem  PKCS8 ES256 private key (per-user mode when set).
 * @param {string|null} opts.iapAudience    expected `aud` of the IAP assertion (required in per-user mode).
 * @param {string|null} opts.staticToken    legacy shared service token (ladder rung 2).
 * @param {*} [opts.iapKeys]                key / JWKS-resolver for verifying assertions
 *                                          (tests inject a local key; defaults to Google's JWKS).
 * @param {() => number} [opts.now]         seconds-since-epoch clock (injectable for tests).
 * @returns {{ mode: string, authorizationFor: (assertion: string|undefined) => Promise<string|null> }}
 */
export function createAuthProvider({ signingKeyPem, iapAudience, staticToken, iapKeys, now }) {
  const clock = now || (() => Date.now() / 1000);

  if (!signingKeyPem) {
    const header = staticToken ? `Bearer ${staticToken}` : null;
    return {
      mode: staticToken ? 'static-token' : 'open',
      authorizationFor: async () => header,
    };
  }

  if (!iapAudience) {
    // Fail fast at startup: a signing key without the expected IAP audience
    // would mean minting identities from unverifiable assertions.
    throw new Error('QUANTUI_SIGNING_KEY is set but QUANTUI_IAP_AUDIENCE is not');
  }

  const keys = iapKeys || createRemoteJWKSet(new URL(IAP_JWKS_URL));
  const privateKeyPromise = importPKCS8(signingKeyPem, 'ES256');
  const cache = new Map(); // email -> { header, exp }

  async function mintFor(email) {
    const cached = cache.get(email);
    const nowSecs = clock();
    if (cached && cached.exp - nowSecs > REMINT_MARGIN_SECONDS) {
      return cached.header;
    }
    const exp = Math.floor(nowSecs) + TOKEN_TTL_SECONDS;
    const token = await new SignJWT({ email })
      .setProtectedHeader({ alg: 'ES256' })
      .setSubject(email)
      .setAudience(USER_TOKEN_AUDIENCE)
      .setIssuedAt(Math.floor(nowSecs))
      .setExpirationTime(exp)
      .sign(await privateKeyPromise);
    const header = `Bearer ${token}`;
    cache.set(email, { header, exp });
    return header;
  }

  return {
    mode: 'per-user',
    authorizationFor: async (assertion) => {
      if (!assertion) {
        throw new IapAuthError('missing IAP assertion');
      }
      let payload;
      try {
        ({ payload } = await jwtVerify(assertion, keys, {
          issuer: IAP_ISSUER,
          audience: iapAudience,
          algorithms: ['ES256'],
        }));
      } catch {
        // Deliberately unchained: jose error text describes the token.
        throw new IapAuthError('invalid IAP assertion');
      }
      // The JWT `email` claim is the plain address; the legacy header form
      // ("accounts.google.com:user@…") is stripped defensively.
      const email = String(payload.email || payload.sub || '').replace(
        /^accounts\.google\.com:/,
        ''
      );
      if (!email) {
        throw new IapAuthError('assertion carries no identity');
      }
      return mintFor(email);
    },
  };
}
