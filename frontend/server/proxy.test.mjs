// Behaviour tests for the /api proxy (#357), against a real local upstream on an ephemeral port.
// Run with `npm test` (node --test) in frontend/server/.

import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import https from 'node:https';
import { spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { createApiProxy } from './proxy.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}

// Spins up `handler` as the upstream and the proxy in front of it (mounted at /api, as in
// server.mjs, with an optional parked bearer). Returns the proxy port and a cleanup.
async function harness(handler, { bearer, tls } = {}) {
  const upstream = tls ? https.createServer(tls, handler) : http.createServer(handler);
  const upstreamPort = await listen(upstream);
  const app = express();
  app.use('/api', (req, _res, next) => { if (bearer) req.quantuiAuthorization = bearer; next(); });
  app.use('/api', createApiProxy(`${tls ? 'https' : 'http'}://127.0.0.1:${upstreamPort}`));
  const front = http.createServer(app);
  const port = await listen(front);
  return {
    port,
    upstreamPort,
    close: () => {
      upstream.closeAllConnections(); front.closeAllConnections();
      upstream.close(); front.close();
    },
  };
}

// A raw client request; resolves with the response once headers arrive.
function request(port, { method = 'GET', path: p = '/api/x', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method, path: p, headers }, resolve);
    req.on('error', reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

async function readAll(res) {
  let data = '';
  for await (const chunk of res) data += chunk;
  return data;
}

// Upstream that echoes what it received as JSON.
function echo(req, res) {
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', () => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ url: req.url, method: req.method, headers: req.headers, body }));
  });
}

test('keeps the /api prefix and the query string', async (t) => {
  const h = await harness(echo); t.after(h.close);
  const got = JSON.parse(await readAll(await request(h.port, { path: '/api/options/chain?symbol=AAPL&x=1' })));
  assert.equal(got.url, '/api/options/chain?symbol=AAPL&x=1');
});

test('sets Host to the upstream host', async (t) => {
  const h = await harness(echo); t.after(h.close);
  const got = JSON.parse(await readAll(await request(h.port, { headers: { host: 'quantui.example' } })));
  assert.equal(got.headers.host, `127.0.0.1:${h.upstreamPort}`);
});

test('sends the parked bearer, never the browser credentials', async (t) => {
  const h = await harness(echo, { bearer: 'Bearer minted' }); t.after(h.close);
  const got = JSON.parse(await readAll(await request(h.port, { headers: {
    authorization: 'Bearer from-browser',
    'x-goog-iap-jwt-assertion': 'iap.jwt.here',
    cookie: 'GCP_IAP_UID=secret',
  } })));
  assert.equal(got.headers.authorization, 'Bearer minted');
  assert.equal(got.headers['x-goog-iap-jwt-assertion'], undefined);
  assert.equal(got.headers.cookie, undefined);
});

test('with no parked bearer, the browser authorization is still dropped', async (t) => {
  const h = await harness(echo); t.after(h.close);
  const got = JSON.parse(await readAll(await request(h.port, { headers: { authorization: 'Bearer from-browser' } })));
  assert.equal(got.headers.authorization, undefined);
});

test('drops hop-by-hop request headers and forwards ordinary ones', async (t) => {
  const h = await harness(echo); t.after(h.close);
  const got = JSON.parse(await readAll(await request(h.port, { headers: {
    connection: 'keep-alive, x-private-hop',
    'x-private-hop': 'nope',
    'proxy-authorization': 'Basic nope',
    te: 'trailers',
    upgrade: 'websocket',
    accept: 'text/event-stream',
    'x-custom': 'yes',
  } })));
  for (const h of ['x-private-hop', 'proxy-authorization', 'te', 'upgrade']) {
    assert.equal(got.headers[h], undefined, `${h} was forwarded`);
  }
  assert.equal(got.headers.accept, 'text/event-stream');
  assert.equal(got.headers['x-custom'], 'yes');
});

test('drops hop-by-hop response headers', async (t) => {
  const h = await harness((_req, res) => {
    res.writeHead(200, { 'keep-alive': 'timeout=5', 'x-up-hop': '1', connection: 'x-up-hop', 'x-kept': '1' });
    res.end('ok');
  }); t.after(h.close);
  const res = await request(h.port);
  await readAll(res);
  assert.equal(res.headers['x-up-hop'], undefined);
  assert.equal(res.headers['x-kept'], '1');
});

test('streams the response: the first chunk arrives before the upstream ends', async (t) => {
  let finish;
  const h = await harness((_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: one\n\n');
    finish = () => res.end('data: two\n\n');
  }); t.after(h.close);
  const res = await request(h.port);
  assert.equal(res.headers['content-type'], 'text/event-stream');
  const [first] = await once(res, 'data');
  assert.equal(String(first), 'data: one\n\n'); // received while the upstream still holds the stream
  finish();
  assert.equal(await readAll(res), 'data: two\n\n');
});

test('streams the request body, fixed-length and chunked', async (t) => {
  const h = await harness(echo); t.after(h.close);
  const fixed = JSON.parse(await readAll(await request(h.port, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"q":"hi"}',
  })));
  assert.equal(fixed.method, 'POST');
  assert.equal(fixed.body, '{"q":"hi"}');

  const chunked = await new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: h.port, method: 'POST', path: '/api/chat' }, resolve);
    req.on('error', reject);
    req.write('part-1;');
    setImmediate(() => { req.write('part-2'); req.end(); });
  });
  assert.equal(JSON.parse(await readAll(chunked)).body, 'part-1;part-2');
});

test('passes upstream error statuses and bodies through unchanged', async (t) => {
  const h = await harness((req, res) => {
    const status = Number(req.url.split('/').pop());
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ detail: `status ${status}` }));
  }); t.after(h.close);
  for (const status of [401, 403, 404, 422]) {
    const res = await request(h.port, { path: `/api/s/${status}` });
    assert.equal(res.statusCode, status);
    assert.deepEqual(JSON.parse(await readAll(res)), { detail: `status ${status}` });
  }
});

test('an unreachable upstream is a terse 502 that logs and returns no URL or token', async (t) => {
  // Grab a free port, then close it so nothing listens there.
  const probe = http.createServer();
  const deadPort = await listen(probe);
  probe.close();

  const app = express();
  app.use('/api', (req, _res, next) => { req.quantuiAuthorization = 'Bearer secret-token'; next(); });
  app.use('/api', createApiProxy(`http://127.0.0.1:${deadPort}`));
  const front = http.createServer(app);
  const port = await listen(front);
  t.after(() => front.close());

  const logged = [];
  t.mock.method(console, 'error', (...args) => logged.push(args.join(' ')));

  const res = await request(port);
  const body = await readAll(res);
  assert.equal(res.statusCode, 502);
  assert.deepEqual(JSON.parse(body), { detail: 'upstream unavailable' });
  for (const text of [body, ...logged]) {
    assert.ok(!text.includes(String(deadPort)), 'leaked the upstream address');
    assert.ok(!text.includes('secret-token'), 'leaked the bearer');
  }
  assert.equal(logged.length, 1);
});

test('a client disconnect aborts the upstream request', async (t) => {
  let upstreamClosed;
  const closed = new Promise((r) => { upstreamClosed = r; });
  const h = await harness((req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: one\n\n'); // then hold the stream open
    res.on('close', upstreamClosed);
  }); t.after(h.close);

  const res = await request(h.port);
  await once(res, 'data');
  res.destroy();
  await closed; // resolves only if the proxy tore down its upstream request
});

// A throwaway self-signed certificate for 127.0.0.1, generated per run -- never committed, so
// no private key sits in the repo for the secret scan to flag. Returns null without openssl.
function selfSignedCert() {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'proxy-tls-'));
  try {
    const run = spawnSync('openssl', [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=127.0.0.1',
      '-addext', 'subjectAltName=IP:127.0.0.1',
      '-keyout', path.join(dir, 'key.pem'), '-out', path.join(dir, 'cert.pem'),
    ], { stdio: 'ignore' });
    if (run.error?.code === 'ENOENT') return null;
    assert.equal(run.status, 0, 'openssl failed to make the test certificate');
    return { key: readFileSync(path.join(dir, 'key.pem')), cert: readFileSync(path.join(dir, 'cert.pem')) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('an https: target streams over verified TLS with the same rewriting', async (t) => {
  const tls = selfSignedCert();
  if (!tls) return t.skip('openssl not found');
  // Trust the fixture the way production trusts quantcore-api's certificate: by verifying it, not
  // by turning verification off. The proxy uses https.globalAgent, which merges its options.
  const previousCa = https.globalAgent.options.ca;
  https.globalAgent.options.ca = tls.cert;
  t.after(() => { https.globalAgent.options.ca = previousCa; https.globalAgent.destroy(); });

  let finish;
  let seen;
  const h = await harness((req, res) => {
    seen = { url: req.url, host: req.headers.host, authorization: req.headers.authorization, cookie: req.headers.cookie };
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: one\n\n');
    finish = () => res.end('data: two\n\n');
  }, { bearer: 'Bearer minted', tls }); t.after(h.close);

  const res = await request(h.port, {
    path: '/api/chat?x=1', headers: { authorization: 'Bearer from-browser', cookie: 'GCP_IAP_UID=secret' },
  });
  assert.equal(res.statusCode, 200);
  const [first] = await once(res, 'data');
  assert.equal(String(first), 'data: one\n\n');
  finish();
  assert.equal(await readAll(res), 'data: two\n\n');
  assert.deepEqual(seen, {
    url: '/api/chat?x=1', host: `127.0.0.1:${h.upstreamPort}`, authorization: 'Bearer minted', cookie: undefined,
  });
});

test('an https: target whose certificate is not trusted is a 502, not an insecure fallback', async (t) => {
  const tls = selfSignedCert();
  if (!tls) return t.skip('openssl not found');
  t.mock.method(console, 'error', () => {});
  let reached = false;
  const h = await harness((_req, res) => { reached = true; res.end('nope'); }, { tls }); t.after(h.close);
  const res = await request(h.port);
  await readAll(res);
  assert.equal(res.statusCode, 502);
  assert.equal(reached, false);
});

test('http-proxy-middleware stays gone', () => {
  const pkg = JSON.parse(readFileSync(path.join(HERE, 'package.json'), 'utf8'));
  assert.equal(pkg.dependencies?.['http-proxy-middleware'], undefined);
  const importers = readdirSync(HERE)
    .filter((f) => /\.(m|c)?js$/.test(f) && !/\.test\.(m|c)?js$/.test(f))
    .filter((f) => /from\s+['"]http-proxy-middleware['"]|require\(\s*['"]http-proxy-middleware/
      .test(readFileSync(path.join(HERE, f), 'utf8')));
  assert.deepEqual(importers, []);
});
