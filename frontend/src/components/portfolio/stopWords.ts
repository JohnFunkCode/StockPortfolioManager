/**
 * The words for a stored stop (#431 step 4.3), and which of a book's stops a
 * holding shows. Every figure is the server's; this only picks and formats.
 */
import { formatCurrency, formatDate } from '../../utils/formatting';
import type { Stop } from '../../api/stops';
import type { Lot } from '../../api/portfolioTypes';

export const METHOD_WORDS: Record<string, string> = {
  manual: 'typed by hand',
  technical: "the stop card's technical stop",
  trailing: "the stop card's trailing stop",
  confluence: "the stop card's confluence stop",
};

/** What prompted a sale, for the Realized view. */
export const SALE_SOURCES: Record<string, string> = {
  manual: 'Manual',
  stop: 'Stop',
  harvester: 'Harvester',
  evaluator: 'Evaluator',
  watch: 'Watch',
  ai: 'AI',
};

/** A percentage as typed: "8%", "7.5%". */
export function trailPercent(pct: number | null): string {
  return pct == null ? '' : `${Number(pct)}%`;
}

/**
 * The stop each held symbol shows: its newest stop when that one is active,
 * or was reached while the holding was already open.
 *
 * `stops` is newest first, as the server sends it. A stop that was reached
 * is shown only for a lot opened on or before the bar that reached it: a
 * position bought back later was never under that stop.
 */
export function stopsBySymbol(stops: Stop[], lotsBySymbol: Record<string, Lot[]>): Record<string, Stop> {
  const shown: Record<string, Stop> = {};
  const seen = new Set<string>();
  for (const stop of stops) {
    if (seen.has(stop.symbol)) continue;
    seen.add(stop.symbol);
    const lots = lotsBySymbol[stop.symbol];
    if (!lots || lots.length === 0) continue;
    if (stop.status === 'active' || reachedWhileHeld(stop, lots)) shown[stop.symbol] = stop;
  }
  return shown;
}

function reachedWhileHeld(stop: Stop, lots: Lot[]): boolean {
  const session = stop.triggered_session;
  if (stop.status !== 'triggered' || session == null) return false;
  return lots.some((lot) => lot.trade_date != null && lot.trade_date.slice(0, 10) <= session);
}

/** One or two sentences about a stop, for its tooltip. `paper` says the stop
 * is in a paper book, where a stop that is reached sells the position; in the
 * real book it only alerts. */
export function stopDetail(stop: Stop, paper = false): string {
  if (stop.status === 'triggered') {
    const reached =
      `A closed bar on ${formatDate(stop.triggered_session)} reached this stop at ` +
      `${formatCurrency(stop.trigger_price)}.`;
    return paper
      ? `${reached} The position was sold at that price.`
      : `${reached} Nothing was sold: a real book's stop is an alert. The holding has no stop now.`;
  }
  const terms =
    stop.kind === 'trailing'
      ? `Trails ${trailPercent(stop.trail_pct)} below a high of ${formatCurrency(stop.high_water_mark)}`
      : 'A fixed level';
  const how = METHOD_WORDS[stop.method] ?? stop.method;
  const checked = stop.last_bar_checked
    ? `Last checked against the bar of ${formatDate(stop.last_bar_checked)}.`
    : stop.first_session
      ? `Not checked yet: the first check is against the bar of ${formatDate(stop.first_session)}, the day its buy filled at the open.`
      : 'Not checked yet: the first check is against the next session to open after it was set.';
  return `${terms}, ${how}, set ${formatDate(stop.created_at.slice(0, 10))}. ${checked}`;
}
