// QuantUI production server — the Cloud Run equivalent of vite.config.ts's dev proxy.
//
// Serves the built static SPA (dist/) AND reverse-proxies /api/* to the JWT-protected
// REST tier (quantcore-api), attaching the bearer token SERVER-SIDE. The token therefore
// never reaches the browser bundle, and the browser stays same-origin (no CORS).
//
// Behind Google IAP on Cloud Run: IAP gates who can load the UI; this server turns the
// IAP-verified identity into the token for the UI->API hop. Since BYOK packet 7b
// (decision #13) that token is PER-USER: the IAP assertion is verified and a short-lived
// ES256 JWT is minted with sub = the user's email (see auth.mjs for the full ladder —
// signing key -> per-user mint; else QUANTCORE_API_TOKEN -> legacy static; else none,
// preserving the open local contract where the api runs AUTH_DISABLED).
//
// Env:
//   PORT                  Cloud Run injects this (default 8080 locally).
//   QUANTCORE_REST_URL    base URL of quantcore-api (default http://127.0.0.1:5001).
//   QUANTUI_SIGNING_KEY   ES256 private key (PKCS8 PEM) for per-user minting — the
//                         quantui-signing-key secret; only this service holds it.
//   QUANTUI_IAP_AUDIENCE  expected `aud` of the IAP assertion (per-project value,
//                         "/projects/<num>/locations/<region>/services/quantui").
//                         Required whenever QUANTUI_SIGNING_KEY is set.
//   QUANTCORE_API_TOKEN   legacy static bearer; used only when no signing key is set.
//   DIST_DIR              override the static root (default ../dist relative to this file).

import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createProxyMiddleware } from 'http-proxy-middleware';
import { createAuthProvider, IapAuthError } from './auth.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = process.env.DIST_DIR || path.resolve(__dirname, '..', 'dist');

const PORT = Number(process.env.PORT) || 8080;
const API_TARGET = process.env.QUANTCORE_REST_URL || 'http://127.0.0.1:5001';
// .trim() guards against a stray trailing newline in the secret value (e.g. when a
// token is piped into `gcloud secrets versions add --data-file=-`): an Authorization
// header with a newline throws ERR_INVALID_CHAR and would crash the proxy.
const API_TOKEN = (process.env.QUANTCORE_API_TOKEN || '').trim();
// \n unescaping lets the PEM be supplied single-line in compose/env files.
const SIGNING_KEY = (process.env.QUANTUI_SIGNING_KEY || '').trim().replace(/\\n/g, '\n');
const IAP_AUDIENCE = (process.env.QUANTUI_IAP_AUDIENCE || '').trim();

// Throws at startup (bilge-pump: loud, immediate) if the signing key is set
// without the IAP audience — that combination could mint unverified identities.
const authProvider = createAuthProvider({
  signingKeyPem: SIGNING_KEY || null,
  iapAudience: IAP_AUDIENCE || null,
  staticToken: API_TOKEN || null,
});

const app = express();

// Strict CSP (BYOK packet 5b): the SPA is fully self-contained (fonts are
// self-hosted via @fontsource), so everything locks to 'self'. The only
// exception is style-src 'unsafe-inline', required by MUI/emotion's runtime
// <style> injection. require-trusted-types-for 'script' turns on Trusted
// Types enforcement so any future DOM XSS sink throws instead of executing.
const CSP =
  "default-src 'self'; " +
  "connect-src 'self'; " +
  "frame-ancestors 'none'; " +
  "object-src 'none'; " +
  "form-action 'self'; " +
  "base-uri 'none'; " +
  "style-src 'self' 'unsafe-inline'; " +
  "require-trusted-types-for 'script'";

app.use((_req, res, next) => {
  res.setHeader('Content-Security-Policy', CSP);
  next();
});

// Lightweight liveness probe (does not touch the api) for Cloud Run / compose.
app.get('/healthz', (_req, res) => res.status(200).send('ok'));

// /api/* -> REST tier, with the bearer attached here (never in the browser).
// onProxyReq is synchronous, so the (possibly async) verify+mint runs in this
// middleware first and parks the header on the request for the proxy to read.
app.use('/api', async (req, res, next) => {
  try {
    req.quantuiAuthorization = await authProvider.authorizationFor(
      req.headers['x-goog-iap-jwt-assertion']
    );
    next();
  } catch (err) {
    if (err instanceof IapAuthError) {
      // Uniform reject; never echoes the assertion or verifier error text.
      res.status(401).json({ detail: 'IAP assertion missing or invalid' });
    } else {
      // e.g. Google JWKS unreachable — fail closed, equally tersely.
      console.error('quantui auth middleware failure:', err?.constructor?.name);
      res.status(503).json({ detail: 'authentication temporarily unavailable' });
    }
  }
});

app.use(
  '/api',
  createProxyMiddleware({
    target: API_TARGET,
    changeOrigin: true,
    onProxyReq: (proxyReq, req) => {
      if (req.quantuiAuthorization) {
        proxyReq.setHeader('authorization', req.quantuiAuthorization);
      }
    },
  })
);

// Static assets, then SPA fallback so client-side routes (react-router) resolve.
app.use(express.static(DIST_DIR));
app.get('*', (_req, res) => res.sendFile(path.join(DIST_DIR, 'index.html')));

app.listen(PORT, () => {
  const auth = {
    'per-user': 'per-user mint (IAP)',
    'static-token': 'static token injected',
    open: 'no token (AUTH_DISABLED parity)',
  }[authProvider.mode];
  console.log(`QuantUI serving ${DIST_DIR} on :${PORT} -> ${API_TARGET} (${auth})`);
});
