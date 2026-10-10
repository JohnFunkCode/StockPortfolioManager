// Pins the reachability claim behind the braces exception in scripts/audit_npm.sh (#354).
// Run with `npm test` (node --test) in frontend/server/.
//
// braces (GHSA-vfj7-8cjw-p6xm, no fixed version) is reached only through
// http-proxy-middleware -> micromatch, and hpm calls micromatch only when it is
// given a glob path filter. Every proxy here is mounted with an options object
// alone, so hpm falls back to the string context '/' and does a prefix match.
// If someone adds a filter, this fails: re-check the advisory, then either
// keep the filter a plain string path or drop the exception's reasoning.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SOURCES = readdirSync(HERE)
  .filter((f) => f.endsWith('.mjs') && !f.endsWith('.test.mjs'))
  .map((f) => [f, readFileSync(path.join(HERE, f), 'utf8')]);

// Call sites only: `createProxyMiddleware(` not preceded by `import {` etc.
const CALL = /\bcreateProxyMiddleware\s*\(\s*(.)/g;

test('the proxy is mounted at least once (the guard is not vacuous)', () => {
  const calls = SOURCES.flatMap(([, src]) => [...src.matchAll(CALL)]);
  assert.ok(calls.length >= 1, 'no createProxyMiddleware call found in frontend/server');
});

test('createProxyMiddleware is never given a path filter', () => {
  for (const [file, src] of SOURCES) {
    for (const m of src.matchAll(CALL)) {
      // hpm 2.x: createProxyMiddleware(context, options) -- a first argument
      // that is not an object literal is a context, which may be a glob.
      assert.equal(m[1], '{', `${file}: createProxyMiddleware's first argument must be the ` +
        'options object, not a path context (see scripts/audit_npm.sh, braces exception)');
    }
    if (!src.includes('createProxyMiddleware')) continue;
    // hpm 2.x `context` option, hpm 3+ `pathFilter`: either can carry a glob.
    assert.doesNotMatch(src, /\b(context|pathFilter)\s*:/,
      `${file}: a proxy path filter reaches micromatch -> braces (GHSA-vfj7-8cjw-p6xm)`);
  }
});
