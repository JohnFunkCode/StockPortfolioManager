/**
 * The words for a paper order (#431): what was asked for, and what became of
 * it. One home, so the Orders list and the order dialog say the same thing.
 * Every figure is the server's; this only formats.
 */
import { formatCurrency, formatDate, formatShares } from '../../utils/formatting';
import type { Order, RejectReason, SeedResult } from '../../api/orders';

export const REJECT_REASONS: Record<RejectReason, string> = {
  stale_quote: 'no recent trade to price it from',
  no_quote: 'no quote for this symbol',
  non_usd: 'not a US-dollar symbol',
  insufficient_cash: 'not enough cash',
  insufficient_shares: 'not enough shares',
  no_open_price: 'no opening price that session',
};

/** A sell rejected for one of these may be a position that can't be quoted. */
export const UNQUOTED_REASONS: RejectReason[] = ['no_quote', 'stale_quote'];

/** "Buy 5 INTC", or "Buy $500.00 of INTC" for a dollar order. A seed copies
 * a real holding in; a manual close sells a position that has no quote; a
 * stop's sale is one the daily check made when a closed bar reached the stop. */
export function request(order: Order): string {
  const shares = formatShares(order.requested_shares);
  if (order.kind === 'seed') return `Copy ${shares} ${order.symbol} from the real book`;
  if (order.kind === 'manual_close') return `Close ${shares} ${order.symbol} by hand`;
  if (order.source === 'stop') return `Sell ${shares} ${order.symbol}, by its stop`;
  const verb = order.side === 'buy' ? 'Buy' : 'Sell';
  const what =
    order.requested_shares != null
      ? `${verb} ${shares} ${order.symbol}`
      : `${verb} ${formatCurrency(order.requested_amount)} of ${order.symbol}`;
  return `${what}${stopAsked(order)}`;
}

/** ", with a stop at $45.00" or ", with a stop trailing 8%": what a buy asked
 * for. Empty for an order that asked for no stop. */
function stopAsked(order: Order): string {
  if (order.stop_kind === 'fixed') return `, with a stop at ${formatCurrency(order.stop_level)}`;
  if (order.stop_kind === 'trailing') return `, with a stop trailing ${Number(order.stop_trail_pct)}%`;
  return '';
}

/** "stop not set: …", for a filled buy whose stop could not be written. A
 * fixed stop gave a level the fill was not above. A trailing stop gave no
 * level: its percentage was too small to reach a price below the fill. */
function stopNotSet(order: Order): string {
  return order.stop_kind === 'trailing'
    ? 'stop not set: the trail is too small to give a price below the fill'
    : 'stop not set: filled at or below it';
}

/** What became of the stop a buy asked for, as a sentence. Empty for an
 * order that asked for none, or was rejected or withdrawn: it never gets one. */
export function stopOutcome(order: Order): string {
  if (order.stop_kind == null) return '';
  if (order.status === 'pending') return 'Its stop is set when the order fills.';
  if (order.status !== 'filled') return '';
  if (order.stop_set === false) return `Its ${stopNotSet(order)}. The position has no stop.`;
  return order.stop_kind === 'fixed'
    ? `Its stop is set at ${formatCurrency(order.stop_level)}.`
    : `Its stop is set, trailing ${Number(order.stop_trail_pct)}% below the fill price.`;
}

/** Where a fill's price came from, as a trailing phrase. */
function priceNote(order: Order): string {
  if (order.kind === 'manual_close') {
    return order.quote_time
      ? `, the last price on record, from ${formatDate(order.quote_time.slice(0, 10))}`
      : ', the last price on record';
  }
  if (order.source === 'stop') {
    // A stop's sale fills at its level, or at the open when the stock
    // opened below the level.
    return order.price_source === 'session_open'
      ? ', the opening price, below its stop'
      : ', its stop level';
  }
  const open = order.price_source === 'session_open' ? ', the opening price' : '';
  return order.kind === 'seed' ? `${open}, at no cost` : open;
}

/** What became of the order, in words. */
export function outcome(order: Order): string {
  switch (order.status) {
    case 'filled':
      return `${formatShares(order.filled_shares)} @ ${formatCurrency(order.fill_price)}${priceNote(order)}${
        order.stop_set === false ? `. S${stopNotSet(order).slice(1)}` : ''
      }`;
    case 'pending':
      return `Waiting for the session of ${formatDate(order.session_date)}`;
    case 'rejected':
      return order.reject_reason
        ? (REJECT_REASONS[order.reject_reason] ?? order.reject_reason)
        : '';
    default:
      return 'Withdrawn';
  }
}

/** "3 holdings", "1 holding". */
function holdings(n: number): string {
  return `${n} ${n === 1 ? 'holding' : 'holdings'}`;
}

/** One sentence per thing that happened when a book was seeded. */
export function seedSummary(result: SeedResult): string[] {
  const lines: string[] = [];
  if (result.filled > 0) lines.push(`Copied ${holdings(result.filled)} at the current price.`);
  if (result.pending > 0) {
    lines.push(
      `${holdings(result.pending)} will be copied at the next session's opening price: the market is closed.`,
    );
  }
  if (result.rejected > 0) {
    lines.push(`${holdings(result.rejected)} could not be priced and ${result.rejected === 1 ? 'was' : 'were'} left out. The Orders list says why.`);
  }
  if (result.skipped.length > 0) lines.push(`Left out: ${result.skipped.join(', ')}.`);
  return lines;
}
