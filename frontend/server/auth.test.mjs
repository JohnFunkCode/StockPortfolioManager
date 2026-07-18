// BYOK packet 7b tests: IAP-assertion verification + per-user ES256 minting
// (auth.mjs). Run with `npm test` (node --test) in frontend/server/.
//
// Fake IAP assertions are minted locally with a second EC keypair standing in
// for Google's; the provider takes the verification key by injection, so no
// network (and no real Google JWKS) is involved.

import test from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import { SignJWT, jwtVerify, decodeJwt } from 'jose';

import {
  createAuthProvider,
  IapAuthError,
  IAP_ISSUER,
  USER_TOKEN_AUDIENCE,
} from './auth.mjs';

const IAP_AUDIENCE = '/projects/000000000000/locations/us-central1/services/quantui';

function keyPair() {
  return generateKeyPairSync('ec', { namedCurve: 'P-256' });
}

// Stand-in for Google's IAP signing key.
const google = keyPair();
// The Express signing key (QUANTUI_SIGNING_KEY).
const signing = keyPair();
const SIGNING_PEM = signing.privateKey
  .export({ type: 'pkcs8', format: 'pem' })
  .toString();

async function fakeAssertion({
  email = 'john@funkyinnovations.com',
  issuer = IAP_ISSUER,
  audience = IAP_AUDIENCE,
  expiresIn = 600,
  key = google.privateKey,
} = {}) {
  const now = Math.floor(Date.now() / 1000);
  return new SignJWT({ email })
    .setProtectedHeader({ alg: 'ES256' })
    .setSubject(`accounts.google.com:${email}`)
    .setIssuer(issuer)
    .setAudience(audience)
    .setIssuedAt(now)
    .setExpirationTime(now + expiresIn)
    .sign(key);
}

function perUserProvider(overrides = {}) {
  return createAuthProvider({
    signingKeyPem: SIGNING_PEM,
    iapAudience: IAP_AUDIENCE,
    staticToken: 'legacy-static-token',
    iapKeys: google.publicKey,
    ...overrides,
  });
}

async function rejectsWithIapError(promise) {
  await assert.rejects(promise, (err) => {
    assert.ok(err instanceof IapAuthError, `expected IapAuthError, got ${err}`);
    return true;
  });
}

// --- per-user mode: verify + mint ------------------------------------------ //

test('valid assertion mints a per-user ES256 token', async () => {
  const provider = perUserProvider();
  assert.equal(provider.mode, 'per-user');

  const header = await provider.authorizationFor(await fakeAssertion());
  assert.match(header, /^Bearer /);

  const token = header.slice('Bearer '.length);
  const { payload, protectedHeader } = await jwtVerify(token, signing.publicKey, {
    audience: 'quantcore-api',
  });
  assert.equal(protectedHeader.alg, 'ES256');
  assert.equal(payload.sub, 'john@funkyinnovations.com');
  assert.deepEqual(payload.aud, USER_TOKEN_AUDIENCE);
  assert.equal(payload.exp - payload.iat, 15 * 60);

  // Also valid for the keyproxy audience.
  await jwtVerify(token, signing.publicKey, { audience: 'quantcore-keyproxy' });
});

test('expired assertion is rejected', async () => {
  const provider = perUserProvider();
  const stale = await fakeAssertion({ expiresIn: -3600 });
  await rejectsWithIapError(provider.authorizationFor(stale));
});

test('wrong-audience assertion is rejected', async () => {
  const provider = perUserProvider();
  const other = await fakeAssertion({
    audience: '/projects/999/locations/us-central1/services/other-app',
  });
  await rejectsWithIapError(provider.authorizationFor(other));
});

test('wrong-issuer assertion is rejected', async () => {
  const provider = perUserProvider();
  const forged = await fakeAssertion({ issuer: 'https://evil.example.com' });
  await rejectsWithIapError(provider.authorizationFor(forged));
});

test('assertion signed by the wrong key is rejected', async () => {
  const provider = perUserProvider();
  const imposter = keyPair();
  const forged = await fakeAssertion({ key: imposter.privateKey });
  await rejectsWithIapError(provider.authorizationFor(forged));
});

test('absent assertion is a hard reject — never falls through to the static token', async () => {
  const provider = perUserProvider(); // staticToken IS configured
  await rejectsWithIapError(provider.authorizationFor(undefined));
  await rejectsWithIapError(provider.authorizationFor(''));
});

test('rejection message never contains the assertion or verifier detail', async () => {
  const provider = perUserProvider();
  const stale = await fakeAssertion({ expiresIn: -3600 });
  try {
    await provider.authorizationFor(stale);
    assert.fail('expected rejection');
  } catch (err) {
    assert.ok(!err.message.includes(stale));
    for (const fragment of ['exp', 'signature', 'JWT', 'claim']) {
      assert.ok(
        !err.message.includes(fragment),
        `error message leaks verifier detail: ${err.message}`
      );
    }
  }
});

test('legacy sub-only identity (accounts.google.com: prefix) is stripped', async () => {
  const provider = perUserProvider();
  const now = Math.floor(Date.now() / 1000);
  // No email claim; identity only in the prefixed sub.
  const assertion = await new SignJWT({})
    .setProtectedHeader({ alg: 'ES256' })
    .setSubject('accounts.google.com:thomas@zoidbergfolio.com')
    .setIssuer(IAP_ISSUER)
    .setAudience(IAP_AUDIENCE)
    .setIssuedAt(now)
    .setExpirationTime(now + 600)
    .sign(google.privateKey);
  const header = await provider.authorizationFor(assertion);
  const payload = decodeJwt(header.slice('Bearer '.length));
  assert.equal(payload.sub, 'thomas@zoidbergfolio.com');
});

test('assertion with no identity claim is rejected', async () => {
  const provider = perUserProvider();
  const now = Math.floor(Date.now() / 1000);
  const anonymous = await new SignJWT({})
    .setProtectedHeader({ alg: 'ES256' })
    .setIssuer(IAP_ISSUER)
    .setAudience(IAP_AUDIENCE)
    .setIssuedAt(now)
    .setExpirationTime(now + 600)
    .sign(google.privateKey);
  await rejectsWithIapError(provider.authorizationFor(anonymous));
});

// --- caching --------------------------------------------------------------- //

test('same user gets the cached token; near expiry triggers a re-mint', async () => {
  let fakeNow = Math.floor(Date.now() / 1000);
  const provider = perUserProvider({ now: () => fakeNow });

  const first = await provider.authorizationFor(await fakeAssertion());
  const again = await provider.authorizationFor(await fakeAssertion());
  assert.equal(again, first, 'fresh token should be served from cache');

  fakeNow += 14 * 60 + 30; // 30s of validity left — inside the re-mint margin
  const reminted = await provider.authorizationFor(await fakeAssertion());
  assert.notEqual(reminted, first, 'near-expiry token should be re-minted');
});

test('different users get different tokens', async () => {
  const provider = perUserProvider();
  const a = await provider.authorizationFor(
    await fakeAssertion({ email: 'john@funkyinnovations.com' })
  );
  const b = await provider.authorizationFor(
    await fakeAssertion({ email: 'thomas@zoidbergfolio.com' })
  );
  assert.notEqual(a, b);
  assert.equal(decodeJwt(a.slice(7)).sub, 'john@funkyinnovations.com');
  assert.equal(decodeJwt(b.slice(7)).sub, 'thomas@zoidbergfolio.com');
});

// --- fallback ladder ------------------------------------------------------- //

test('no signing key + static token -> legacy static header, assertion ignored', async () => {
  const provider = createAuthProvider({
    signingKeyPem: null,
    iapAudience: null,
    staticToken: 'legacy-static-token',
  });
  assert.equal(provider.mode, 'static-token');
  assert.equal(await provider.authorizationFor(undefined), 'Bearer legacy-static-token');
  assert.equal(
    await provider.authorizationFor(await fakeAssertion()),
    'Bearer legacy-static-token'
  );
});

test('no signing key + no static token -> no header (AUTH_DISABLED parity)', async () => {
  const provider = createAuthProvider({
    signingKeyPem: null,
    iapAudience: null,
    staticToken: null,
  });
  assert.equal(provider.mode, 'open');
  assert.equal(await provider.authorizationFor(undefined), null);
});

test('signing key without IAP audience fails fast at startup', () => {
  assert.throws(
    () =>
      createAuthProvider({
        signingKeyPem: SIGNING_PEM,
        iapAudience: null,
        staticToken: null,
      }),
    /QUANTUI_SIGNING_KEY is set but QUANTUI_IAP_AUDIENCE is not/
  );
});
