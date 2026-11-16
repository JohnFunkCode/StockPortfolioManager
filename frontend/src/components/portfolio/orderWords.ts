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
 * a real holding in; a manual close sells a position that has no quote. */
export function request(order: Order): string {
  const shares = formatShares(order.requested_shares);
  if (order.kind === 'seed') return `Copy ${shares} ${order.symbol} from the real book`;
  if (order.kind === 'manual_close') return `Close ${shares} ${order.symbol} by hand`;
  const verb = order.side === 'buy' ? 'Buy' : 'Sell';
  if (order.requested_shares != null) return `${verb} ${shares} ${order.symbol}`;
  return `${verb} ${formatCurrency(order.requested_amount)} of ${order.symbol}`;
}

/** Where a fill's price came from, as a trailing phrase. */
function priceNote(order: Order): string {
  if (order.kind === 'manual_close') {
    return order.quote_time
      ? `, the last price on record, from ${formatDate(order.quote_time.slice(0, 10))}`
      : ', the last price on record';
  }
  const open = order.price_source === 'session_open' ? ', the opening price' : '';
  return order.kind === 'seed' ? `${open}, at no cost` : open;
}

/** What became of the order, in words. */
export function outcome(order: Order): string {
  switch (order.status) {
    case 'filled':
      return `${formatShares(order.filled_shares)} @ ${formatCurrency(order.fill_price)}${priceNote(order)}`;
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
