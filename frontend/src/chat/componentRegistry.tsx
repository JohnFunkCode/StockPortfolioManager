/**
 * The chat sidekick's component vocabulary — the only things an LLM directive
 * can render. Keep names + prop specs in sync with the backend registry in
 * quantcore/services/chat_tools.py (defense in depth: both sides validate).
 */
import type { ComponentType } from 'react';
import SignalsTab from '../components/securities/SignalsTab';
import LivePrice from '../components/symbols/LivePrice';
import PriceChartCard from '../components/chat/PriceChartCard';
import SpreadPayoffCard from '../components/chat/SpreadPayoffCard';
import ArbitrageCard from '../components/chat/ArbitrageCard';
import {
  SpreadHistoryCard,
  PremiumHistoryCard,
  ArbScanCard,
  DiscoveryCard,
} from '../components/chat/ArbitrageChartCards';
import PortfolioTableCard from '../components/chat/PortfolioTableCard';
import PortfolioAllocationCard from '../components/chat/PortfolioAllocationCard';
import SymbolLotsCard from '../components/chat/SymbolLotsCard';
import WatchlistFundamentalsCard from '../components/chat/WatchlistFundamentalsCard';
import {
  FundamentalsTopCard,
  FundamentalsScoreChangesCard,
} from '../components/chat/FundamentalsCards';
import type { ChatDirective } from './types';

type PropKind = 'string' | 'number';

export interface RegistryEntry {
  /** Self-contained component: receives the validated props and fetches its own data. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  component: ComponentType<any>;
  /** Required props and their kinds — extras are strictly rejected. */
  spec: Record<string, PropKind>;
  /**
   * Render a "TICKER — Name" header above the component in chat. Panels that
   * carry no symbol of their own need this so multi-ticker conversations stay
   * unambiguous; components with built-in titles (spread_payoff) skip it.
   */
  titled?: boolean;
}

const TICKER_ONLY: Record<string, PropKind> = { ticker: 'string' };

export const COMPONENT_REGISTRY: Record<string, RegistryEntry> = {
  signals: { component: SignalsTab, spec: TICKER_ONLY, titled: true },
  live_price: { component: LivePrice, spec: TICKER_ONLY },
  price_chart: { component: PriceChartCard, spec: TICKER_ONLY, titled: true },
  spread_payoff: {
    component: SpreadPayoffCard,
    spec: {
      ticker: 'string',
      expiration: 'string',
      long_strike: 'number',
      short_strike: 'number',
      kind: 'string',
    },
  },
  // Carries its own "SECURITY vs UNDERLYING" heading, so no titled wrapper.
  arbitrage_pair: { component: ArbitrageCard, spec: TICKER_ONLY },
  arbitrage_spread: { component: SpreadHistoryCard, spec: TICKER_ONLY },
  arbitrage_premium: { component: PremiumHistoryCard, spec: TICKER_ONLY },
  // Universe-wide: no props at all.
  arbitrage_scan: { component: ArbScanCard, spec: {}, titled: false },
  arbitrage_discovery: { component: DiscoveryCard, spec: { symbols: 'string' } },
  // No props: always the caller's own portfolio via the browser's
  // authenticated session — never add an `owner` prop (decision #20).
  portfolio_table: { component: PortfolioTableCard, spec: {}, titled: false },
  // Same no-props rule, for the same reason (issue #147).
  portfolio_allocation: { component: PortfolioAllocationCard, spec: {}, titled: false },
  // Carries its own "TICKER — Lots" heading, so no titled wrapper.
  symbol_lots: { component: SymbolLotsCard, spec: TICKER_ONLY },
  // The watchlist is one global list — there is no symbol or owner to name.
  watchlist_fundamentals: { component: WatchlistFundamentalsCard, spec: {}, titled: false },
  // Both cover the whole tracked universe — no symbol and no owner to name.
  fundamentals_top: { component: FundamentalsTopCard, spec: {}, titled: false },
  fundamentals_score_changes: { component: FundamentalsScoreChangesCard, spec: {}, titled: false },
};

/**
 * The interaction vocabulary — which user gestures each component may send
 * back into the conversation. Mirrors BACKEND_INTERACTION_REGISTRY in
 * quantcore/services/chat_tools.py (both sides validate, like directives).
 *
 * mode 'context': the gesture attaches to the NEXT typed message.
 * mode 'message': the gesture submits a structured turn immediately.
 */
export interface InteractionSpec {
  payload: Record<string, PropKind>;
  mode: 'context' | 'message';
}

export const INTERACTION_REGISTRY: Record<string, Record<string, InteractionSpec>> = {
  spread_payoff: {
    select_strike: { payload: { strike: 'number' }, mode: 'context' },
    reprice_leg: { payload: { leg: 'string', strike: 'number' }, mode: 'message' },
  },
  price_chart: {
    select_point: { payload: { date: 'string', price: 'number' }, mode: 'context' },
  },
};

export type ValidationResult = { ok: true } | { ok: false; reason: string };

export function validateInteractionPayload(
  component: string,
  action: string,
  payload: Record<string, unknown>,
  registry: Record<string, Record<string, InteractionSpec>> = INTERACTION_REGISTRY,
): ValidationResult {
  const spec = registry[component]?.[action];
  if (!spec) {
    return { ok: false, reason: `No interaction '${action}' on '${component}'` };
  }
  const extra = Object.keys(payload).filter((k) => !(k in spec.payload));
  if (extra.length > 0) {
    return { ok: false, reason: `Unexpected payload fields: ${extra.join(', ')}` };
  }
  for (const [name, kind] of Object.entries(spec.payload)) {
    const value = payload[name];
    if (kind === 'string') {
      if (typeof value !== 'string' || value.trim().length === 0) {
        return { ok: false, reason: `Payload '${name}' must be a non-empty string` };
      }
    } else if (typeof value !== 'number' || !Number.isFinite(value)) {
      return { ok: false, reason: `Payload '${name}' must be a finite number` };
    }
  }
  return { ok: true };
}

export function validateDirective(
  directive: ChatDirective,
  registry: Record<string, RegistryEntry> = COMPONENT_REGISTRY,
): ValidationResult {
  const entry = registry[directive.component];
  if (!entry) {
    return {
      ok: false,
      reason: `Unknown component '${directive.component}'. Valid: ${Object.keys(registry).sort().join(', ')}`,
    };
  }
  const props = directive.props;
  if (typeof props !== 'object' || props === null || Array.isArray(props)) {
    return { ok: false, reason: 'props must be an object' };
  }
  const extra = Object.keys(props).filter((k) => !(k in entry.spec));
  if (extra.length > 0) {
    return { ok: false, reason: `Unexpected props: ${extra.join(', ')}` };
  }
  for (const [name, kind] of Object.entries(entry.spec)) {
    if (!(name in props)) {
      return { ok: false, reason: `Missing required prop '${name}'` };
    }
    const value = (props as Record<string, unknown>)[name];
    if (kind === 'string') {
      if (typeof value !== 'string' || value.trim().length === 0) {
        return { ok: false, reason: `Prop '${name}' must be a non-empty string` };
      }
    } else if (typeof value !== 'number' || !Number.isFinite(value)) {
      return { ok: false, reason: `Prop '${name}' must be a finite number` };
    }
  }
  return { ok: true };
}
