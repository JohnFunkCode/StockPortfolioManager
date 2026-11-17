/**
 * The words for a stored stop (#431 step 4.3). Every figure is the server's,
 * and so is the choice of which stop a holding shows; this only formats.
 */
import { formatCurrency, formatDate } from '../../utils/formatting';
import type { Stop } from '../../api/stops';

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

/** One or two sentences about a stop, for its tooltip. */
export function stopDetail(stop: Stop): string {
  if (stop.status === 'triggered') {
    return (
      `A closed bar on ${formatDate(stop.triggered_session)} reached this stop at ` +
      `${formatCurrency(stop.trigger_price)}. Nothing was sold: a real book's stop is an alert. ` +
      'The holding has no stop now.'
    );
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
