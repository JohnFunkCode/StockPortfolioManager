// The environment tag in index.html and its caching (#437), wired as server.mjs wires it.
// Run with `npm test` (node --test) in frontend/server/.

import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import express from 'express';
import { createIndexHandler, injectEnvironment, resolveEnvironment } from './indexHtml.mjs';

const INDEX = '<!doctype html>\n<html>\n  <head>\n    <title>QuantUI</title>\n  </head>\n  <body></body>\n</html>\n';
const TAG = /<meta name="quantui-environment" content="([^"]*)"/;

// A dist/ with index.html and one hashed asset, served by the same middleware order as
// server.mjs. Returns a GET helper and a cleanup.
async function serve(rawEnvironment) {
  const dist = mkdtempSync(path.join(os.tmpdir(), 'quantui-dist-'));
  writeFileSync(path.join(dist, 'index.html'), INDEX);
  mkdirSync(path.join(dist, 'assets'));
  writeFileSync(path.join(dist, 'assets', 'index-abc123.js'), 'console.log(1)');
  const index = createIndexHandler(dist, rawEnvironment);
  const app = express();
  app.use(express.static(dist, { index: false }));
  app.get('*', index.handler);
  const server = http.createServer(app);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    environment: index.environment,
    get: async (p) => {
      const res = await fetch(base + p);
      return { status: res.status, headers: res.headers, body: await res.text() };
    },
    close: () => { server.closeAllConnections(); server.close(); rmSync(dist, { recursive: true }); },
  };
}

test('resolveEnvironment accepts only test and prod', () => {
  assert.equal(resolveEnvironment('test'), 'test');
  assert.equal(resolveEnvironment(' prod\n'), 'prod');
  for (const raw of [undefined, '', 'TEST', 'staging', '"><script>']) {
    assert.equal(resolveEnvironment(raw), null, String(raw));
  }
});

test('injectEnvironment leaves the page alone without an environment', () => {
  assert.equal(injectEnvironment(INDEX, null), INDEX);
});

test('injectEnvironment refuses a page with no </head>', () => {
  assert.throws(() => injectEnvironment('<html><body></body></html>', 'test'), /no <\/head>/);
});

test('test: the home page and a deep link carry the tag, uncached', async (t) => {
  const s = await serve('test');
  t.after(s.close);
  assert.equal(s.environment, 'test');
  // `/` matters most: without { index: false } express.static would answer it untouched.
  for (const p of ['/', '/plans/1']) {
    const res = await s.get(p);
    assert.equal(res.status, 200, p);
    assert.equal(res.body.match(TAG)?.[1], 'test', p);
    assert.ok(res.body.indexOf('quantui-environment') < res.body.indexOf('</head>'), p);
    assert.equal(res.headers.get('cache-control'), 'no-cache', p);
    assert.match(res.headers.get('content-type'), /text\/html/, p);
  }
});

test('prod carries prod', async (t) => {
  const s = await serve('prod');
  t.after(s.close);
  assert.equal((await s.get('/')).body.match(TAG)?.[1], 'prod');
});

test('unset or unknown: no tag at all', async (t) => {
  for (const raw of [undefined, 'staging']) {
    const s = await serve(raw);
    t.after(s.close);
    const res = await s.get('/');
    assert.equal(s.environment, null);
    assert.doesNotMatch(res.body, /quantui-environment/);
    assert.equal(res.headers.get('cache-control'), 'no-cache');
  }
});

test('hashed assets keep express.static caching, not no-cache', async (t) => {
  const s = await serve('test');
  t.after(s.close);
  const res = await s.get('/assets/index-abc123.js');
  assert.equal(res.status, 200);
  assert.equal(res.body, 'console.log(1)');
  assert.notEqual(res.headers.get('cache-control'), 'no-cache');
});
