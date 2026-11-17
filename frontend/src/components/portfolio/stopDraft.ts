/**
 * A stop as a form holds it, and the stop card's candidates as drafts (#431
 * steps 4.3 and 4.5). Shared by the Buy dialog's stop field and the stop
 * panel on the Security page. Every figure is the server's: this only words
 * them and turns a draft into a payload.
 */
import { formatCurrency } from '../../utils/formatting';
import { NO_SUPPORT_BELOW_PRICE } from '../../api/securitiesTypes';
import type { StopMethod } from '../../api/stops';
import type { StopLossResponse } from '../../api/securitiesTypes';

/** The stop as the form holds it. `method` and `proposed` remember which of
 * the card's candidates filled the field, if one did: the figure can then be
 * edited, and the stop records both what was suggested and what was saved. */
export interface StopDraft {
  kind: 'fixed' | 'trailing';
  value: string;
  method: StopMethod;
  proposed: number | null;
}

export const EMPTY_STOP: StopDraft = { kind: 'fixed', value: '', method: 'manual', proposed: null };

/** The terms of a draft as a request carries them. */
export interface StopTermsPayload {
  kind: 'fixed' | 'trailing';
  stop_level?: number;
  trail_pct?: number;
  method: StopMethod;
  proposed_level?: number;
}

/** The payload for a draft, or the reason it can't be sent. */
export function stopPayload(draft: StopDraft): { stop: StopTermsPayload } | { error: string } {
  const number = Number(draft.value);
  const fixed = draft.kind === 'fixed';
  if (!draft.value.trim() || !Number.isFinite(number) || number <= 0) {
    return { error: fixed ? 'The stop level must be more than zero.' : 'The trail must be more than zero percent.' };
  }
  if (!fixed && number >= 100) return { error: 'The trail must be less than 100 percent.' };
  return {
    stop: {
      kind: draft.kind,
      ...(fixed ? { stop_level: number } : { trail_pct: number }),
      method: draft.method,
      ...(draft.proposed != null ? { proposed_level: draft.proposed } : {}),
    },
  };
}

/** One of the stop card's candidates: a draft, its label, and the card's
 * reasoning for it in words. */
export interface StopSuggestion extends StopDraft {
  label: string;
  reason: string;
}

const pct = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;

function technicalReason(data: StopLossResponse): string {
  const t = data.technical;
  const anchor =
    t.primary_support === NO_SUPPORT_BELOW_PRICE
      ? 'with no support below the price'
      : `under the ${t.primary_support.replace(/_/g, ' ')} at ${formatCurrency(t.primary_support_price)}`;
  const noise = data.stops.technical_stop_inside_noise_floor
    ? ` Inside the noise floor: closer than the worst one-day drop (${data.drawdown.max_1day_pct.toFixed(1)}%).`
    : '';
  return `${pct(data.stops.technical_stop_distance_pct)} from the price, ${anchor}.${noise}`;
}

/** The stop card's candidates, as drafts. The confluence stop is there only
 * when a zone qualified. */
export function stopSuggestions(data: StopLossResponse): StopSuggestion[] {
  const stops = data.stops;
  const found: StopSuggestion[] = [
    {
      label: `Technical ${formatCurrency(stops.technical_stop)}`,
      kind: 'fixed', value: String(stops.technical_stop), method: 'technical',
      proposed: stops.technical_stop,
      reason: technicalReason(data),
    },
    {
      label: `Trailing ${stops.trailing_stop_pct}%`,
      kind: 'trailing', value: String(stops.trailing_stop_pct), method: 'trailing',
      proposed: stops.trailing_stop_price,
      reason:
        `Trails ${stops.trailing_stop_pct.toFixed(1)}% below the high, ${formatCurrency(stops.trailing_stop_price)} ` +
        `if placed today: the drawdown history's width (${data.drawdown.base_trailing_stop_pct.toFixed(1)}%), ` +
        'adjusted for short interest.',
    },
  ];
  if (stops.confluence_stop) {
    const zone = stops.confluence_stop;
    found.push({
      label: `Confluence ${formatCurrency(zone.stop)}`,
      kind: 'fixed', value: String(zone.stop), method: 'confluence',
      proposed: zone.stop,
      reason:
        `${pct(zone.distance_pct)} from the price, under the support zone ` +
        `${formatCurrency(zone.zone_low)}–${formatCurrency(zone.zone_high)}, where ` +
        `${zone.method_count} methods agree.`,
    });
  }
  return found;
}
