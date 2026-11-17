// The Content-Security-Policy QuantUI sends, and the one exception to it.
//
// Strict CSP (BYOK packet 5b): the SPA is fully self-contained (fonts are
// self-hosted via @fontsource), so everything locks to 'self'. The only
// exception is style-src 'unsafe-inline', required by MUI/emotion's runtime
// <style> injection. require-trusted-types-for 'script' turns on Trusted
// Types enforcement so any future DOM XSS sink throws instead of executing.
export const CSP =
  "default-src 'self'; " +
  "connect-src 'self'; " +
  "frame-ancestors 'none'; " +
  "object-src 'none'; " +
  "form-action 'self'; " +
  "base-uri 'none'; " +
  "style-src 'self' 'unsafe-inline'; " +
  "require-trusted-types-for 'script'";

// Self-contained research pages shown inside the SPA in a sandboxed <iframe>. These are
// pre-built HTML documents that carry their own inline <script> (the Wind Map does,
// and writes markup with innerHTML) and inline <style>, so they cannot run under CSP
// above. Instead of loosening CSP for the whole app, each listed page gets
// SANDBOXED_PAGE_CSP, whose `sandbox` directive (without
// allow-same-origin) puts the document in an opaque origin even when opened directly:
// it cannot read QuantUI's cookies, storage or BYOK vault, or call /api as the user.
//
// The match is on the exact, undecoded request path. Any other spelling of the path
// (encoded, trailing slash) gets the strict CSP, so a miss breaks the page rather than
// weakening anything.
export const SANDBOXED_PAGES = new Set([
  '/research/wind-map.html', // Sector Wind Map (Market Casting prototype), the /wind-map page
]);

// Inline script and style only; no network at all (connect/img/font fall to 'none',
// so the page's Google Fonts links are refused and it uses its fallback fonts).
// frame-ancestors 'self' lets the SPA frame it and nothing else.
export const SANDBOXED_PAGE_CSP =
  'sandbox allow-scripts; ' +
  "default-src 'none'; " +
  "script-src 'unsafe-inline'; " +
  "style-src 'unsafe-inline'; " +
  'img-src data:; ' +
  "frame-ancestors 'self'; " +
  "base-uri 'none'; " +
  "form-action 'none'";

/** The CSP for a request path. */
export function cspFor(requestPath) {
  return SANDBOXED_PAGES.has(requestPath) ? SANDBOXED_PAGE_CSP : CSP;
}

/** Express middleware: set the CSP on every response, before any route answers. */
export function cspMiddleware(req, res, next) {
  res.setHeader('Content-Security-Policy', cspFor(req.path));
  next();
}
