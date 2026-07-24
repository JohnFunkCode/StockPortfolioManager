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

/** N days of P/C history rows for PCRatioChart. */
export function pcHistoryRows(n = 30) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(2026, 3, (i % 27) + 1);
    rows.push({
      captured_at: d.toISOString(),
      price: 100 + i * 0.3,
      put_call_ratio: 0.8 + 0.4 * Math.sin(i / 5),
      bb_upper: 108,
      bb_middle: 100,
      bb_lower: 92,
    });
  }
  return rows;
}

/** Option contracts across a strike ladder (both sides). */
export function optionContracts(strikes = [95, 100, 105, 110], spot = 102) {
  const rows: Array<Record<string, unknown>> = [];
  let id = 1;
  for (const strike of strikes) {
    for (const kind of ['call', 'put'] as const) {
      rows.push({
        contract_id: id++,
        expiration_id: 1,
        kind,
        strike,
        last_price: 2.5,
        bid: 2.3,
        ask: 2.7,
        implied_vol: 0.45,
        volume: 100 + strike,
        open_interest: 500 + strike,
        in_the_money: (kind === 'call' ? (strike < spot ? 1 : 0) : (strike > spot ? 1 : 0)) as 0 | 1,
      });
    }
  }
  return rows;
}

/** An OptionsSnapshot with one expiration for OptionsChainChart. */
export function optionsSnapshot(symbol = 'INTC') {
  const contracts = optionContracts();
  return {
    snapshot_id: 1,
    symbol,
    captured_at: '2026-07-24T15:00:00Z',
    price: 102,
    bb_upper: 110,
    bb_middle: 100,
    bb_lower: 90,
    bb_period: 20,
    expirations: [
      {
        expiration_id: 1,
        snapshot_id: 1,
        expiration: '2026-08-21',
        put_call_ratio: 1.1,
        total_call_oi: 5000,
        total_put_oi: 5500,
        total_call_vol: 1200,
        total_put_vol: 1400,
        avg_call_iv: 44,
        avg_put_iv: 48,
        contracts,
      },
    ],
  };
}

/** Term-structure expirations for IVTermStructureChart. */
export function ivExpirations() {
  return [
    { expiration: '2026-08-21', avg_call_iv: 42, avg_put_iv: 46, total_call_oi: 1, total_put_oi: 1, put_call_ratio: 1, total_call_vol: 1, total_put_vol: 1, expiration_id: 1, snapshot_id: 1, contracts: [] },
    { expiration: '2026-09-18', avg_call_iv: 40, avg_put_iv: 44, total_call_oi: 1, total_put_oi: 1, put_call_ratio: 1, total_call_vol: 1, total_put_vol: 1, expiration_id: 2, snapshot_id: 1, contracts: [] },
    { expiration: '2026-12-18', avg_call_iv: 38, avg_put_iv: 41, total_call_oi: 1, total_put_oi: 1, put_call_ratio: 1, total_call_vol: 1, total_put_vol: 1, expiration_id: 3, snapshot_id: 1, contracts: [] },
  ];
}

/** A plan instance row. */
export function planRow(overrides: object = {}) {
  return {
    instance_id: 1,
    symbol: 'INTC',
    status: 'ACTIVE',
    created_at: '2026-07-01T00:00:00Z',
    shares_initial: 100,
    h_threshold: 0.1,
    n_iterations: 4,
    ...overrides,
  };
}

/** A rung row. */
export function rungRow(overrides: object = {}) {
  return {
    rung_id: 1,
    instance_id: 1,
    rung_index: 1,
    target_price: 120,
    shares_before: 100,
    shares_sold_planned: 10,
    shares_after_planned: 90,
    expected_days_from_now: 30,
    expected_date: '2026-08-24',
    gross_harvest_planned: 1200,
    cumulative_harvest_planned: 1200,
    remaining_value_planned: 10800,
    total_wealth_planned: 12000,
    total_return_planned: 0.2,
    status: 'PENDING',
    triggered_at: null,
    trigger_price: null,
    executed_at: null,
    executed_price: null,
    shares_sold_actual: null,
    gross_harvest_actual: null,
    tax_paid_actual: null,
    net_harvest_actual: null,
    notes: null,
    ...overrides,
  };
}

/** Max-pain curve points. */
export function painCurve() {
  return [90, 95, 100, 105, 110].map((strike, i) => ({
    strike,
    pain: 100000 - Math.abs(i - 2) * 20000,
  }));
}
