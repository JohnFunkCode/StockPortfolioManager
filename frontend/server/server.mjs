// QuantUI production server — the Cloud Run equivalent of vite.config.ts's dev proxy.
//
// Serves the built static SPA (dist/) AND reverse-proxies /api/* to the JWT-protected
// REST tier (quantcore-api), injecting the bearer token SERVER-SIDE from the
// QUANTCORE_API_TOKEN env (a Secret Manager secret on Cloud Run). The token therefore
// never reaches the browser bundle, and the browser stays same-origin (no CORS).
//
// Behind Google IAP on Cloud Run: IAP gates who can load the UI; this server holds the
// service identity that authenticates the UI->API hop. When QUANTCORE_API_TOKEN is unset
// (local docker-compose, where the api runs AUTH_DISABLED), no Authorization header is
// added, preserving the open local contract.
//
// Env:
//   PORT                Cloud Run injects this (default 8080 locally).
//   QUANTCORE_REST_URL  base URL of quantcore-api (default http://127.0.0.1:5001).
//   QUANTCORE_API_TOKEN bearer token presented to the api; omitted from requests if unset.
//   DIST_DIR            override the static root (default ../dist relative to this file).

import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createProxyMiddleware } from 'http-proxy-middleware';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = process.env.DIST_DIR || path.resolve(__dirname, '..', 'dist');

const PORT = Number(process.env.PORT) || 8080;
const API_TARGET = process.env.QUANTCORE_REST_URL || 'http://127.0.0.1:5001';
// .trim() guards against a stray trailing newline in the secret value (e.g. when a
// token is piped into `gcloud secrets versions add --data-file=-`): an Authorization
// header with a newline throws ERR_INVALID_CHAR and would crash the proxy.
const API_TOKEN = (process.env.QUANTCORE_API_TOKEN || '').trim();

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

// /api/* -> REST tier, with the bearer injected here (never in the browser).
app.use(
  '/api',
  createProxyMiddleware({
    target: API_TARGET,
    changeOrigin: true,
    onProxyReq: (proxyReq) => {
      if (API_TOKEN) {
        proxyReq.setHeader('authorization', `Bearer ${API_TOKEN}`);
      }
    },
  })
);

// Static assets, then SPA fallback so client-side routes (react-router) resolve.
app.use(express.static(DIST_DIR));
app.get('*', (_req, res) => res.sendFile(path.join(DIST_DIR, 'index.html')));

app.listen(PORT, () => {
  const auth = API_TOKEN ? 'token injected' : 'no token (AUTH_DISABLED parity)';
  console.log(`QuantUI serving ${DIST_DIR} on :${PORT} -> ${API_TARGET} (${auth})`);
});
