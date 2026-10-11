// The /api reverse proxy: a small node:http/node:https pipe to quantcore-api (#357).
//
// It replaced http-proxy-middleware, whose micromatch -> braces chain (GHSA-vfj7-8cjw-p6xm, no
// fixed version) was the frontend's only npm audit exception. With no dependency here, the server
// lock audits clean and scripts/audit_npm.sh carries no exceptions.
//
// What it does, all pinned by proxy.test.mjs:
//   - streams both bodies (req.pipe / upstreamRes.pipe), never buffers -- Sidekick chat is SSE;
//   - keeps the /api prefix (req.originalUrl; Express strips the mount from req.url);
//   - sets Host to the target's host (changeOrigin);
//   - sends only the bearer the auth middleware in server.mjs parked on req.quantuiAuthorization,
//     never the browser's authorization, IAP assertion or cookies (quantcore-api reads none);
//   - drops hop-by-hop headers both ways;
//   - answers a terse 502 when the upstream fails, and aborts the upstream when the client leaves.
//
// No timeout: Cloud Run's request timeout bounds a request, and chat goes quiet during tool calls.
// Never log a URL, header, token or error message here -- only the error's code or class name.

import http from 'node:http';
import https from 'node:https';

// RFC 9110 section 7.6.1 connection-specific headers, plus host (rewritten below).
export const HOP_BY_HOP = Object.freeze([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade',
]);

// Request headers never forwarded upstream: the browser's credentials. The api's bearer is set
// only from req.quantuiAuthorization.
export const REQUEST_DENYLIST = Object.freeze([
  ...HOP_BY_HOP, 'host', 'authorization', 'x-goog-iap-jwt-assertion', 'cookie',
]);

function stripHeaders(headers, denylist) {
  // Headers named in Connection are hop-by-hop for this message too.
  const named = String(headers.connection || '')
    .split(',').map((h) => h.trim().toLowerCase()).filter(Boolean);
  const drop = new Set([...denylist, ...named]);
  const out = {};
  for (const [name, value] of Object.entries(headers)) {
    if (!drop.has(name.toLowerCase())) out[name] = value;
  }
  return out;
}

export function createApiProxy(target) {
  const base = new URL(target);
  const transport = base.protocol === 'https:' ? https : http;
  const basePath = base.pathname.replace(/\/+$/, '');

  return function apiProxy(req, res) {
    const headers = stripHeaders(req.headers, REQUEST_DENYLIST);
    headers.host = base.host;
    if (req.quantuiAuthorization) headers.authorization = req.quantuiAuthorization;

    const upstreamReq = transport.request({
      protocol: base.protocol,
      hostname: base.hostname,
      port: base.port || undefined,
      method: req.method,
      path: basePath + req.originalUrl,
      headers,
    });

    upstreamReq.on('response', (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode, stripHeaders(upstreamRes.headers, HOP_BY_HOP));
      upstreamRes.pipe(res);
      upstreamRes.on('error', () => res.destroy());
    });

    upstreamReq.on('error', (err) => {
      if (res.destroyed || res.writableEnded) return; // the client left; nothing to tell it
      console.error('quantui proxy upstream failure:', err?.code || err?.constructor?.name);
      if (res.headersSent) {
        res.destroy();
      } else {
        res.statusCode = 502;
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({ detail: 'upstream unavailable' }));
      }
    });

    // The client went away before the response finished: stop the upstream work too.
    res.on('close', () => {
      if (!res.writableFinished) upstreamReq.destroy();
    });

    req.pipe(upstreamReq);
  };
}
