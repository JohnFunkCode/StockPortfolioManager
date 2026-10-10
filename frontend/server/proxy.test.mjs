// Pins the reachability claim behind the braces exception in scripts/audit_npm.sh (#354).
// Run with `npm test` (node --test) in frontend/server/.
//
// braces (GHSA-vfj7-8cjw-p6xm, no fixed version) is reached only through
// http-proxy-middleware -> micromatch, and hpm calls micromatch only when it is
// given a glob path filter. The checks run on the RESOLVED options object, so a
// filter added indirectly (a spread, an imported object) fails as well as an
// inline one. If one fails: re-check the advisory, then either keep the filter a
// plain string path or drop the exception's reasoning.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ALLOWED_OPTION_KEYS, apiProxyOptions, createApiProxy } from './proxy.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

test('the resolved proxy options carry only the allowed keys (no path filter)', () => {
  const keys = Object.keys(apiProxyOptions('http://api.invalid')).sort();
  assert.deepEqual(keys, [...ALLOWED_OPTION_KEYS].sort(),
    'a new proxy option needs the braces exception re-checked (scripts/audit_npm.sh)');
  for (const filter of ['context', 'pathFilter']) {
    assert.ok(!ALLOWED_OPTION_KEYS.includes(filter),
      `${filter} reaches micromatch -> braces (GHSA-vfj7-8cjw-p6xm)`);
  }
});

test('onProxyReq attaches the parked bearer, and nothing when there is none', () => {
  const { onProxyReq } = apiProxyOptions('http://api.invalid');
  const set = [];
  const proxyReq = { setHeader: (k, v) => set.push([k, v]) };
  onProxyReq(proxyReq, { quantuiAuthorization: 'Bearer x' });
  onProxyReq(proxyReq, {});
  assert.deepEqual(set, [['authorization', 'Bearer x']]);
});

test('createApiProxy builds a middleware', () => {
  assert.equal(typeof createApiProxy('http://api.invalid'), 'function');
});

test('proxy.mjs is the only module that reaches http-proxy-middleware', () => {
  // Anything else importing hpm could mount a proxy the checks above never see.
  const importers = readdirSync(HERE)
    .filter((f) => /\.(m|c)?js$/.test(f) && !/\.test\.(m|c)?js$/.test(f))
    .filter((f) => readFileSync(path.join(HERE, f), 'utf8').includes('http-proxy-middleware'));
  assert.deepEqual(importers, ['proxy.mjs']);

  // ...and inside it, the one call takes the checked options as its only argument
  // (hpm 2.x reads a first argument that is not the options object as a context).
  const calls = [...readFileSync(path.join(HERE, 'proxy.mjs'), 'utf8')
    .matchAll(/\bcreateProxyMiddleware\s*\(([^;]*)\);/g)].map((m) => m[1].trim());
  assert.deepEqual(calls, ['apiProxyOptions(target)']);
});
