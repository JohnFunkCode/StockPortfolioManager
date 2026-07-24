/**
 * Shared test harness for component/hook suites (frontend 85%-campaign).
 *
 * Two seams:
 *  - renderWithProviders(): QueryClient (no retries, no cache persistence) +
 *    MemoryRouter, with optional `path` for useParams-driven pages.
 *  - mockApi(): stubs global.fetch — the single conduit apiRequest and
 *    chatStream use — from an ordered [matcher -> payload] table. Unmatched
 *    requests 404 loudly (react-query error branches are then deliberate).
 *
 * Keep suites black-box: mock the network, not the hooks, unless a component
 * test genuinely needs hook-level isolation (see SpreadPayoffCard.test.tsx
 * for that older, hook-mocked style).
 */
import type { ReactElement, ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, renderHook, type RenderOptions } from '@testing-library/react';
import { vi } from 'vitest';

// ---------------------------------------------------------------------------
// fetch mocking
// ---------------------------------------------------------------------------

type Matcher = string | RegExp;
type Responder =
  | object
  | ((url: string, init?: RequestInit) => object | { __status: number });

export interface MockedApi {
  /** Every fetch the code under test made: [url, init]. */
  calls: Array<[string, RequestInit | undefined]>;
  /** URLs that matched no route (and were 404ed). */
  unmatched: string[];
}

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * Stub global.fetch from an ordered route table. First matching entry wins.
 *   mockApi([
 *     ['/api/securities/INTC/technicals', { ticker: 'INTC', indicators: [...] }],
 *     [/\/api\/securities$/, { securities: [] }],
 *   ])
 * A responder function may return `{ __status: 500, ...body }` to force a status.
 * Cleanup is automatic via vi.unstubAllGlobals() in afterEach (vitest config
 * `unstubGlobals` is not enabled globally — call it in the suite's afterEach).
 */
export function mockApi(routes: Array<[Matcher, Responder]>): MockedApi {
  const state: MockedApi = { calls: [], unmatched: [] };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      state.calls.push([url, init]);
      for (const [matcher, responder] of routes) {
        const hit =
          typeof matcher === 'string' ? url.includes(matcher) : matcher.test(url);
        if (!hit) continue;
        const body =
          typeof responder === 'function' ? responder(url, init) : responder;
        const { __status = 200, ...rest } = body as { __status?: number };
        return jsonResponse(rest, __status);
      }
      state.unmatched.push(url);
      return jsonResponse({ error: `no mock for ${url}` }, 404);
    }),
  );
  return state;
}

// ---------------------------------------------------------------------------
// providers
// ---------------------------------------------------------------------------

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

interface ProviderOptions extends Omit<RenderOptions, 'wrapper'> {
  /** Initial URL, e.g. '/securities/INTC'. */
  route?: string;
  /** Route pattern when the component reads useParams, e.g. '/securities/:symbol'. */
  path?: string;
  queryClient?: QueryClient;
}

function Providers({
  children,
  route = '/',
  path,
  queryClient,
}: ProviderOptions & { children: ReactNode }) {
  const qc = queryClient ?? makeQueryClient();
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        {path ? (
          <Routes>
            <Route path={path} element={children} />
          </Routes>
        ) : (
          children
        )}
      </MemoryRouter>
    </QueryClientProvider>
  );
}

export function renderWithProviders(ui: ReactElement, options: ProviderOptions = {}) {
  const { route, path, queryClient, ...renderOptions } = options;
  return render(ui, {
    wrapper: ({ children }) => (
      <Providers route={route} path={path} queryClient={queryClient}>
        {children}
      </Providers>
    ),
    ...renderOptions,
  });
}

export function renderHookWithProviders<T>(hook: () => T, options: ProviderOptions = {}) {
  const { route, path, queryClient } = options;
  return renderHook(hook, {
    wrapper: ({ children }) => (
      <Providers route={route} path={path} queryClient={queryClient}>
        {children}
      </Providers>
    ),
  });
}

// ---------------------------------------------------------------------------
// fixtures shared across suites
// ---------------------------------------------------------------------------

/** N business days of TechnicalIndicator rows with sane, plottable values. */
export function indicatorRows(n = 60, base = 100) {
  const rows = [];
  const start = new Date('2026-04-01T00:00:00Z');
  let day = 0;
  for (let i = 0; i < n; i++) {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const d = new Date(start.getTime() + day * 86_400_000);
      day += 1;
      if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) {
        const close = base + i * 0.5 + 3 * Math.sin(i / 4);
        rows.push({
          date: d.toISOString().slice(0, 10),
          close,
          volume: 1_000_000 + i,
          ma10: close - 1,
          ma30: close - 2,
          ma50: close - 3,
          ma100: close - 4,
          ma200: close - 5,
          bb_upper: close + 4,
          bb_middle: close,
          bb_lower: close - 4,
          rsi: 45 + 10 * Math.sin(i / 5),
          macd: Math.sin(i / 6),
          macd_signal: Math.sin((i - 1) / 6),
          macd_hist: 0.1 * Math.sin(i / 3),
        });
        break;
      }
    }
  }
  return rows;
}

export function securityRow(symbol = 'INTC', overrides: object = {}) {
  return {
    symbol,
    name: `${symbol} Corp`,
    source: 'watchlist',
    tags: [],
    currency: 'USD',
    ...overrides,
  };
}
