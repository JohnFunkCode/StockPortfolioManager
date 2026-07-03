/**
 * The chat sidekick's component vocabulary — the only things an LLM directive
 * can render. Keep names + validation in sync with the backend registry in
 * quantcore/services/chat_tools.py (defense in depth: both sides validate).
 */
import type { ComponentType } from 'react';
import SignalsTab from '../components/securities/SignalsTab';
import LivePrice from '../components/symbols/LivePrice';
import PriceChartCard from '../components/chat/PriceChartCard';
import type { ChatDirective } from './types';

export interface RegistryEntry {
  /** Self-contained component: takes a ticker and fetches its own data. */
  component: ComponentType<{ ticker: string }>;
}

export const COMPONENT_REGISTRY: Record<string, RegistryEntry> = {
  signals: { component: SignalsTab },
  live_price: { component: LivePrice },
  price_chart: { component: PriceChartCard },
};

export type ValidationResult = { ok: true } | { ok: false; reason: string };

export function validateDirective(
  directive: ChatDirective,
  registry: Record<string, RegistryEntry> = COMPONENT_REGISTRY,
): ValidationResult {
  if (!registry[directive.component]) {
    return {
      ok: false,
      reason: `Unknown component '${directive.component}'. Valid: ${Object.keys(registry).sort().join(', ')}`,
    };
  }
  const props = directive.props;
  if (typeof props !== 'object' || props === null || Array.isArray(props)) {
    return { ok: false, reason: 'props must be an object' };
  }
  const extra = Object.keys(props).filter((k) => k !== 'ticker');
  if (extra.length > 0) {
    return { ok: false, reason: `Unexpected props: ${extra.join(', ')}` };
  }
  const ticker = (props as { ticker?: unknown }).ticker;
  if (typeof ticker !== 'string' || ticker.trim().length === 0) {
    return { ok: false, reason: "Prop 'ticker' must be a non-empty string" };
  }
  return { ok: true };
}
