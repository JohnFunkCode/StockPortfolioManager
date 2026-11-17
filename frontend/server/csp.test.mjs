// The CSP each response carries, through cspMiddleware + mountSpa — the same wiring
// server.mjs uses. Run with `npm test` (node --test) in frontend/server/.

import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import express from 'express';
import { CSP, SANDBOXED_PAGES, SANDBOXED_PAGE_CSP, cspFor, cspMiddleware } from './csp.mjs';
import { mountSpa } from './indexHtml.mjs';

const WIND_MAP = '/research/wind-map.html';

async function serve() {
  const dist = mkdtempSync(path.join(os.tmpdir(), 'quantui-csp-'));
  writeFileSync(path.join(dist, 'index.html'), '<html><head></head><body></body></html>');
  mkdirSync(path.join(dist, 'research'));
  writeFileSync(path.join(dist, 'research', 'wind-map.html'), '<script>1</script>');
  writeFileSync(path.join(dist, 'research', 'other.html'), '<script>1</script>');
  const app = express();
  app.use(cspMiddleware);
  mountSpa(app, dist, undefined);
  const server = http.createServer(app);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    get: async (p) => {
      const res = await fetch(base + p);
      await res.text();
      return { status: res.status, csp: res.headers.get('content-security-policy') };
    },
    close: () => { server.closeAllConnections(); server.close(); rmSync(dist, { recursive: true }); },
  };
}

test('the app CSP stays strict', () => {
  assert.match(CSP, /default-src 'self'/);
  assert.match(CSP, /frame-ancestors 'none'/);
  assert.match(CSP, /require-trusted-types-for 'script'/);
  assert.doesNotMatch(CSP, /script-src/);
});

test('the sandbox CSP isolates the page from the app origin', () => {
  // allow-same-origin would give the framed page QuantUI's cookies and storage.
  assert.match(SANDBOXED_PAGE_CSP, /^sandbox allow-scripts;/);
  assert.doesNotMatch(SANDBOXED_PAGE_CSP, /allow-same-origin/);
  assert.match(SANDBOXED_PAGE_CSP, /default-src 'none'/);
  assert.match(SANDBOXED_PAGE_CSP, /frame-ancestors 'self'/);
  assert.doesNotMatch(SANDBOXED_PAGE_CSP, /connect-src/);
});

test('cspFor matches only the exact listed paths', () => {
  assert.deepEqual([...SANDBOXED_PAGES], [WIND_MAP]);
  assert.equal(cspFor(WIND_MAP), SANDBOXED_PAGE_CSP);
  for (const p of ['/', '/wind-map', '/research/', '/research/wind-map.html/',
    '/research/wind%2Dmap.html', '/RESEARCH/wind-map.html', '/research/other.html',
    '/portfolio-check', '/research/portfolio-check-sample.html']) {
    assert.equal(cspFor(p), CSP, p);
  }
});

test('served responses carry the right CSP', async () => {
  const s = await serve();
  try {
    const page = await s.get(WIND_MAP);
    assert.equal(page.status, 200);
    assert.equal(page.csp, SANDBOXED_PAGE_CSP);

    for (const p of ['/', '/index.html', '/wind-map', '/portfolio-check', '/research/other.html']) {
      const res = await s.get(p);
      assert.equal(res.status, 200, p);
      assert.equal(res.csp, CSP, p);
    }
  } finally {
    s.close();
  }
});
