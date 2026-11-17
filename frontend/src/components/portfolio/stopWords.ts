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

/** How a broker's order type reads (step 4.5). */
export const BROKER_ORDER_WORDS: Record<string, string> = {
  stop: 'stop',
  stop_limit: 'stop-limit',
  trailing_stop: 'trailing stop',
};

/** Where a stop lives, in words: "Placed at Schwab as a stop-limit order for
 * 100 shares." for one the owner mirrored from a broker, or "" for a stop
 * that exists only here. Only what the owner gave is said. */
export function brokerWords(stop: Stop): string {
  if (!stop.at_broker) return '';
  const where = stop.brokerage ? ` at ${stop.brokerage}` : ' at a broker';
  const how = stop.broker_order_type
    ? ` as a ${BROKER_ORDER_WORDS[stop.broker_order_type] ?? stop.broker_order_type} order`
    : '';
  const size = stop.broker_quantity != null ? ` for ${Number(stop.broker_quantity)} shares` : '';
  return `Placed${where}${how}${size}.`;
}

/** A percentage as typed: "8%", "7.5%". */
export function trailPercent(pct: number | null): string {
  return pct == null ? '' : `${Number(pct)}%`;
}

/** One or two sentences about a stop, for its tooltip. `paper` says the stop
 * is in a paper book, where a stop that is reached sells the position; in the
 * real book it only alerts. */
export function stopDetail(stop: Stop, paper = false): string {
  if (stop.status === 'triggered') {
    const reached =
      `A closed bar on ${formatDate(stop.triggered_session)} reached this stop at ` +
      `${formatCurrency(stop.trigger_price)}.`;
    if (paper) return `${reached} The position was sold at that price.`;
    return stop.at_broker
      ? `${reached} ${brokerWords(stop)} That order should have filled: record the sale. The holding has no stop now.`
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
  const broker = brokerWords(stop);
  return `${terms}, ${how}, set ${formatDate(stop.created_at.slice(0, 10))}. ${broker ? `${broker} ` : ''}${checked}`;
}
