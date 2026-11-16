/**
 * OrdersList — a paper book's orders (#431 step 3.3), inside the Cash and
 * orders section: what was asked for, and what became of it.
 *
 * A row-bound part of `CashLedger`, which mounts it for a paper book only:
 * the real book has no orders. One request, `GET /api/portfolio/orders`.
 * Every figure arrives as the server stored it (Rule 8.4); the only work
 * here is formatting, and turning a status or a rejection code into words.
 *
 * A filled order's money is in the cash entries above, so this table shows
 * no amounts of its own.
 */
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { useCancelOrder, useOrders } from '../../hooks/useOrders';
import { formatCurrency, formatDate, formatShares } from '../../utils/formatting';
import type { Order, RejectReason } from '../../api/orders';

interface Props {
  bookId: number;
  /** Show Cancel on a pending order. */
  canCancel?: boolean;
}

/** How many orders show before "Show all orders". */
export const RECENT_ORDERS = 5;

const REJECT_REASONS: Record<RejectReason, string> = {
  stale_quote: 'no recent trade to price it from',
  no_quote: 'no quote for this symbol',
  non_usd: 'not a US-dollar symbol',
  insufficient_cash: 'not enough cash',
  insufficient_shares: 'not enough shares',
  no_open_price: 'no opening price that session',
};

type Hue = 'green' | 'red' | 'amber' | 'blue';

const STATUS: Record<Order['status'], { label: string; hue: Hue }> = {
  pending: { label: 'Pending', hue: 'blue' },
  filled: { label: 'Filled', hue: 'green' },
  rejected: { label: 'Rejected', hue: 'red' },
  cancelled: { label: 'Cancelled', hue: 'amber' },
};

/** "Buy 5 INTC", or "Buy $500.00 of INTC" for a dollar order. */
function request(order: Order): string {
  const verb = order.side === 'buy' ? 'Buy' : 'Sell';
  if (order.requested_shares != null) {
    return `${verb} ${formatShares(order.requested_shares)} ${order.symbol}`;
  }
  return `${verb} ${formatCurrency(order.requested_amount)} of ${order.symbol}`;
}

/** What became of the order, in words. */
function outcome(order: Order): string {
  switch (order.status) {
    case 'filled':
      return `${formatShares(order.filled_shares)} @ ${formatCurrency(order.fill_price)}${
        order.price_source === 'session_open' ? ', the opening price' : ''
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

function OrderRow({ order, bookId, canCancel }: { order: Order; bookId: number; canCancel: boolean }) {
  const cancel = useCancelOrder(bookId);
  const status = STATUS[order.status];
  return (
    <TableRow hover data-testid={`order-${order.order_id}`}>
      <TableCell>{formatDate(order.session_date)}</TableCell>
      <TableCell>{request(order)}</TableCell>
      <TableCell>
        <Chip
          size="small"
          label={status.label}
          sx={{ bgcolor: `var(--qc-tint-${status.hue})`, color: `var(--qc-ink-${status.hue})` }}
        />
      </TableCell>
      <TableCell>
        {outcome(order)}
        {cancel.error && (
          <Typography variant="caption" color="error" sx={{ display: 'block' }}>
            {(cancel.error as Error).message}
          </Typography>
        )}
      </TableCell>
      {canCancel && (
        <TableCell align="right">
          {order.status === 'pending' && (
            <Button
              size="small"
              color="inherit"
              aria-label={`Cancel order to ${request(order).toLowerCase()}`}
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(order.order_id)}
            >
              Cancel
            </Button>
          )}
        </TableCell>
      )}
    </TableRow>
  );
}

/** "3 orders", "1 order". */
function orders(n: number): string {
  return `${n} ${n === 1 ? 'order' : 'orders'}`;
}

export default function OrdersList({ bookId, canCancel = false }: Props) {
  const { data, isLoading, error } = useOrders(bookId);
  const [showAll, setShowAll] = useState(false);

  let body;
  if (isLoading) {
    body = (
      <Stack direction="row" spacing={1} alignItems="center">
        <CircularProgress size={16} />
        <Typography variant="body2" color="text.secondary">Loading orders…</Typography>
      </Stack>
    );
  } else if (error || !data) {
    body = (
      <Alert severity="error">
        Couldn't load orders: {(error as Error | null)?.message ?? 'no data'}
      </Alert>
    );
  } else if (data.orders.length === 0) {
    body = (
      <Typography variant="body2" color="text.secondary">
        No orders in this book yet.
      </Typography>
    );
  } else {
    const shown = showAll ? data.orders : data.orders.slice(0, RECENT_ORDERS);
    body = (
      <>
        {data.pending_count > 0 && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {orders(data.pending_count)} placed outside market hours{' '}
            {data.pending_count === 1 ? 'is' : 'are'} waiting for{' '}
            {data.pending_count === 1 ? 'its' : 'their'} session. Nothing fills a waiting order
            yet: until that is built it stays pending, and can be cancelled.
          </Alert>
        )}
        <Paper sx={{ overflowX: 'auto' }}>
          <Table size="small" data-testid="orders-table">
            <TableHead>
              <TableRow>
                <TableCell>Session</TableCell>
                <TableCell>Order</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Result</TableCell>
                {canCancel && <TableCell />}
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((order) => (
                <OrderRow key={order.order_id} order={order} bookId={bookId} canCancel={canCancel} />
              ))}
            </TableBody>
          </Table>
        </Paper>
        {data.orders.length > RECENT_ORDERS && (
          <Button size="small" sx={{ mt: 1 }} onClick={() => setShowAll((all) => !all)}>
            {showAll ? 'Show the latest orders' : `Show all ${orders(data.orders.length)}`}
          </Button>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          Newest first. The system sets every fill price. A filled order's cash is in the entries
          above.
        </Typography>
      </>
    );
  }

  return (
    <Box data-testid="orders-list" sx={{ mt: 3 }}>
      <Typography variant="h6" component="h3" sx={{ mb: 1 }}>Orders</Typography>
      {body}
    </Box>
  );
}
