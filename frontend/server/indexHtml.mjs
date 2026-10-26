// The SPA's index.html, carrying which environment this is (#437).
//
// One image is built for test and promoted to prod unchanged, so the environment can't be
// baked in at build time — it is QUANTUI_ENVIRONMENT, set per project in
// deploy/cloudrun-services.toml, and reaches the page as
//   <meta name="quantui-environment" content="test">
// A <meta> and not an inline <script>: the CSP (server.mjs) has no script-src beyond 'self',
// so an injected script would never run — and loosening the CSP for one flag isn't worth it.
//
// Only a known value is injected (no escaping to get wrong); anything else injects nothing,
// and the front end shows no badge without the tag — the badge fails closed.

import { readFileSync } from 'node:fs';
import path from 'node:path';

export const ENVIRONMENTS = new Set(['test', 'prod']);

/** The environment name if it is a known one, else null. */
export function resolveEnvironment(raw) {
  const value = (raw || '').trim();
  return ENVIRONMENTS.has(value) ? value : null;
}

/** index.html with the environment's <meta> added just before </head>. */
export function injectEnvironment(html, environment) {
  if (!environment) return html;
  if (!html.includes('</head>')) {
    // Loud at startup rather than a test deploy that quietly shows no badge.
    throw new Error('index.html has no </head>; cannot inject quantui-environment');
  }
  return html.replace(
    '</head>',
    `  <meta name="quantui-environment" content="${environment}" />\n  </head>`,
  );
}

/**
 * The SPA handler for `/` and every client-side route. Reads index.html once, at startup.
 * `no-cache` makes the browser revalidate the page on every load, so a change of value is
 * never hidden behind a cached copy; the hashed /assets/* keep their normal caching.
 */
export function createIndexHandler(distDir, rawEnvironment) {
  const environment = resolveEnvironment(rawEnvironment);
  const html = injectEnvironment(readFileSync(path.join(distDir, 'index.html'), 'utf8'), environment);
  const handler = (_req, res) => {
    res.setHeader('Cache-Control', 'no-cache');
    res.type('html').send(html);
  };
  return { environment, handler };
}
