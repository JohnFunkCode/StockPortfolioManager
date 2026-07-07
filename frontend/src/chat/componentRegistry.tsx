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
import type { ChatDirective } from './types';

type PropKind = 'string' | 'number';

export interface RegistryEntry {
  /** Self-contained component: receives the validated props and fetches its own data. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  component: ComponentType<any>;
  /** Required props and their kinds — extras are strictly rejected. */
  spec: Record<string, PropKind>;
}

const TICKER_ONLY: Record<string, PropKind> = { ticker: 'string' };

export const COMPONENT_REGISTRY: Record<string, RegistryEntry> = {
  signals: { component: SignalsTab, spec: TICKER_ONLY },
  live_price: { component: LivePrice, spec: TICKER_ONLY },
  price_chart: { component: PriceChartCard, spec: TICKER_ONLY },
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
};

export type ValidationResult = { ok: true } | { ok: false; reason: string };

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
