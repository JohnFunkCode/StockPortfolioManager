// The /api reverse proxy's configuration, kept in one place so proxy.test.mjs can check the
// resolved options rather than the source text (#354).
//
// Why that matters: braces (GHSA-vfj7-8cjw-p6xm, no fixed version) is reached only through
// http-proxy-middleware -> micromatch, and hpm calls micromatch only when it is given a glob path
// filter (hpm 2.x: a context first argument or a `context` option; hpm 3+: `pathFilter`). These
// options carry no filter, so hpm falls back to the string context '/' and does a prefix match,
// and no request input reaches braces. That is the reasoning behind the braces exception in
// scripts/audit_npm.sh; removing hpm altogether is #357.

import { createProxyMiddleware } from 'http-proxy-middleware';

// Every key the options may carry. A key outside this list -- a filter, or anything spread in
// from elsewhere -- fails proxy.test.mjs until someone re-checks the braces exception.
export const ALLOWED_OPTION_KEYS = Object.freeze(['target', 'changeOrigin', 'onProxyReq']);

export function apiProxyOptions(target) {
  return {
    target,
    changeOrigin: true,
    // The bearer was parked on the request by the auth middleware in server.mjs (onProxyReq is
    // synchronous, so the async verify+mint has to run before it).
    onProxyReq: (proxyReq, req) => {
      if (req.quantuiAuthorization) {
        proxyReq.setHeader('authorization', req.quantuiAuthorization);
      }
    },
  };
}

export function createApiProxy(target) {
  return createProxyMiddleware(apiProxyOptions(target));
}
