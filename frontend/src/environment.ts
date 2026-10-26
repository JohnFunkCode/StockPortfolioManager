/**
 * Which deployment this page came from, for the top bar's TEST badge (#437).
 *
 * Read at runtime from the `<meta name="quantui-environment">` that
 * `frontend/server/indexHtml.mjs` injects, never from `import.meta.env`: prod
 * runs the bundle built for test, so a build-time value would say "test" there.
 * A missing tag (Vite dev, compose) or an unknown value is `null` — no badge.
 */
export type QuantuiEnvironment = 'test' | 'prod';

export function getQuantuiEnvironment(doc: Document = document): QuantuiEnvironment | null {
  const value = doc.querySelector('meta[name="quantui-environment"]')?.getAttribute('content');
  return value === 'test' || value === 'prod' ? value : null;
}
