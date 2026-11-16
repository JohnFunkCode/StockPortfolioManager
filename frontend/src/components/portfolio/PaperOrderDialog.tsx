/**
 * PaperOrderDialog — buy or sell on paper (#431 step 3.5).
 *
 * A form, not a display of analytical data: it sends a symbol, a side and a
 * size, never a price or a date. The server sets every fill, so the dialog
 * ends by saying what the server did: filled, queued for the next session, or
 * rejected with the reason.
 *
 * A sell that can't be priced because the symbol has no quote offers one more
 * step: close the whole position at the last price on record. The server
 * decides whether that is allowed and what the price is.
 */
import { useRef, useState } from 'react';
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { useCashLedger } from '../../hooks/useCash';
import { useManualClose, usePlaceOrder } from '../../hooks/useOrders';
import { newIdempotencyKey } from '../../api/orders';
import { formatCurrency, formatDate, formatShares } from '../../utils/formatting';
import { outcome, REJECT_REASONS, UNQUOTED_REASONS } from './orderWords';
import type { Book } from '../../api/books';
import type { Order } from '../../api/orders';

interface Props {
  symbol: string;
  side: 'buy' | 'sell';
  /** The paper books the order can go to. A sell names exactly one. */
  books: Book[];
  /** A sell: the shares the book holds, which fill the field. */
  heldShares?: number | null;
  /** A sell: the position has no current price, so offer the manual close
   * at once instead of after a rejected order. */
  unpriced?: boolean;
  onClose: () => void;
}

/** What the server did with the order, as one sentence. */
function resultText(order: Order): string {
  if (order.status === 'filled') {
    const verb = order.kind === 'manual_close' ? 'Closed' : order.side === 'buy' ? 'Bought' : 'Sold';
    return `${verb} ${order.symbol}: ${outcome(order)}.`;
  }
  if (order.status === 'pending') {
    return `Queued. The market is closed, so this order waits for the session of ${formatDate(
      order.session_date,
    )} and fills at its opening price. Until then it can be cancelled from the Orders list.`;
  }
  const reason = order.reject_reason
    ? (REJECT_REASONS[order.reject_reason] ?? order.reject_reason)
    : 'no reason given';
  return `Rejected: ${reason}. Nothing changed.`;
}

const SEVERITY = { filled: 'success', pending: 'info', rejected: 'warning', cancelled: 'info' } as const;

export default function PaperOrderDialog({
  symbol,
  side,
  books,
  heldShares = null,
  unpriced = false,
  onClose,
}: Props) {
  const buying = side === 'buy';
  const [bookId, setBookId] = useState<number | ''>(books[0]?.book_id ?? '');
  const [unit, setUnit] = useState<'shares' | 'amount'>('shares');
  const [size, setSize] = useState(!buying && heldShares != null ? String(heldShares) : '');
  const [formError, setFormError] = useState('');
  // One key per distinct request: a retry after a network error repeats it,
  // and a changed form gets a new one.
  const key = useRef(newIdempotencyKey());
  const closeKey = useRef(newIdempotencyKey());
  const changed = () => {
    key.current = newIdempotencyKey();
    setFormError('');
  };

  const place = usePlaceOrder();
  const manualClose = useManualClose();
  const { data: cash } = useCashLedger(bookId === '' ? null : bookId);
  const cashKnown = buying && bookId !== '' && cash?.book_kind === 'paper';

  const result = manualClose.data ?? place.data;
  const busy = place.isPending || manualClose.isPending;
  const offerClose =
    !buying &&
    !manualClose.data &&
    (unpriced ||
      (place.data?.status === 'rejected' &&
        place.data.reject_reason != null &&
        UNQUOTED_REASONS.includes(place.data.reject_reason)));

  const submit = () => {
    const number = Number(size);
    if (bookId === '') {
      setFormError('Choose a paper book.');
      return;
    }
    if (!size.trim() || !Number.isFinite(number) || number <= 0) {
      setFormError(unit === 'shares' ? 'Shares must be more than zero.' : 'The amount must be more than zero.');
      return;
    }
    place.mutate(
      {
        book_id: bookId,
        symbol,
        side,
        ...(unit === 'shares' ? { shares: number } : { amount: number }),
        idempotency_key: key.current,
      },
      // The server answered, so this request is over: a second try after a
      // rejection is a new order, not a replay of the rejected one.
      { onSuccess: () => { key.current = newIdempotencyKey(); } },
    );
  };

  const closeByHand = () => {
    if (bookId === '') return;
    manualClose.mutate({ book_id: bookId, symbol, idempotency_key: closeKey.current });
  };

  const error = formError || ((place.error ?? manualClose.error) as Error | null)?.message;
  // Once an order is filled or queued there is nothing left to send.
  const done = result != null && result.status !== 'rejected';

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{buying ? 'Buy' : 'Sell'} {symbol} on paper</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            A simulated order. The system sets the price: the live quote in market hours, or the
            next session's opening price when the market is closed.
          </Typography>
          {!done && (
            <>
              {buying && (
                <FormControl size="small" fullWidth>
                  <InputLabel id="paper-order-book">Paper book</InputLabel>
                  <Select
                    labelId="paper-order-book"
                    label="Paper book"
                    value={bookId}
                    onChange={(e) => {
                      setBookId(e.target.value as number);
                      changed();
                    }}
                  >
                    {books.map((b) => (
                      <MenuItem key={b.book_id} value={b.book_id}>{b.name}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}
              {buying && (
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={unit}
                  onChange={(_, next) => {
                    if (!next) return;
                    setUnit(next);
                    setSize('');
                    changed();
                  }}
                  aria-label="Order size in"
                >
                  <ToggleButton value="shares">Shares</ToggleButton>
                  <ToggleButton value="amount">Dollars</ToggleButton>
                </ToggleButtonGroup>
              )}
              <TextField
                label={unit === 'shares' ? 'Shares' : 'Dollar amount'}
                size="small"
                type="number"
                value={size}
                onChange={(e) => {
                  setSize(e.target.value);
                  changed();
                }}
                inputProps={{ min: 0, step: unit === 'shares' ? 'any' : 0.01 }}
                autoFocus
              />
              {cashKnown && (
                <Typography variant="caption" color="text.secondary">
                  Cash in this book: {formatCurrency(cash?.balance ?? 0)}. A paper book has no margin.
                </Typography>
              )}
              {!buying && heldShares != null && (
                <Typography variant="caption" color="text.secondary">
                  This book holds {formatShares(heldShares)} {symbol}. Shares are sold oldest lot first.
                </Typography>
              )}
            </>
          )}
          {result && (
            <Alert severity={SEVERITY[result.status]} data-testid="order-result">
              {resultText(result)}
            </Alert>
          )}
          {offerClose && (
            <Alert
              severity="info"
              action={
                <Button color="inherit" size="small" disabled={busy} onClick={closeByHand}>
                  {manualClose.isPending ? 'Closing…' : 'Close position'}
                </Button>
              }
            >
              If {symbol} can no longer be quoted (delisted, say), the whole position can be closed
              at the last price on record. The system picks the price.
            </Alert>
          )}
          {error && (
            <Typography variant="caption" role="alert" sx={{ color: 'var(--qc-ink-red)' }}>
              {error}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} disabled={busy}>{done ? 'Done' : 'Cancel'}</Button>
        {!done && (
          <Button variant="contained" onClick={submit} disabled={busy || books.length === 0}>
            {place.isPending ? 'Sending…' : buying ? 'Buy' : 'Sell'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
