/**
 * OrderStopField — the stop a paper buy asks for (#431 step 4.3).
 *
 * Part of the Buy form, not a display of analysis: it collects a level or a
 * trailing percentage. The stop card's three candidates are offered as
 * buttons that fill the field. That request is made only when this field is
 * mounted, which is when the owner opens it, so a buy with no stop never
 * pays for the analysis. Every suggested figure is the server's.
 *
 * A book has one stop a symbol. When the symbol already has one in the
 * chosen book, the field says the new stop will replace it (decision 65).
 */
import { Alert, Button, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { useStopLoss } from '../../hooks/useSecurities';
import { useHoldingStops } from '../../hooks/useStops';
import { formatCurrency } from '../../utils/formatting';
import { EMPTY_STOP, stopSuggestions } from './stopDraft';
import type { StopDraft } from './stopDraft';

interface Props {
  symbol: string;
  /** The paper book the buy is for, or null while none is picked. */
  bookId: number | null;
  draft: StopDraft;
  onChange: (draft: StopDraft) => void;
}

export default function OrderStopField({ symbol, bookId, draft, onChange }: Props) {
  const card = useStopLoss(symbol, 'stop');
  const fixed = draft.kind === 'fixed';
  // A book has one stop a symbol, so a stop set here replaces the one the
  // holding already has. The owner is told before the order is sent.
  const { data: held } = useHoldingStops(bookId, bookId !== null);
  const current = held?.stops.find((stop) => stop.symbol === symbol && stop.status === 'active');

  return (
    <Stack spacing={1.5} data-testid="order-stop">
      {current && (
        <Alert severity="warning" data-testid="order-stop-replaces">
          {symbol} already has a stop in this book, at {formatCurrency(current.stop_level)}
          {current.kind === 'trailing' ? `, trailing ${Number(current.trail_pct)}%` : ''}. The
          stop set here replaces it when the buy fills, for the whole position.
        </Alert>
      )}
      <ToggleButtonGroup
        size="small"
        exclusive
        value={draft.kind}
        onChange={(_, next) => {
          // A new kind is a new stop: the old figure means nothing in it.
          if (next) onChange({ ...EMPTY_STOP, kind: next });
        }}
        aria-label="Stop kind"
      >
        <ToggleButton value="fixed">Fixed level</ToggleButton>
        <ToggleButton value="trailing">Trailing</ToggleButton>
      </ToggleButtonGroup>
      <TextField
        label={fixed ? 'Stop level' : 'Trail percent'}
        size="small"
        type="number"
        value={draft.value}
        onChange={(e) => onChange({ ...draft, value: e.target.value })}
        inputProps={{ min: 0, step: fixed ? 0.01 : 0.1 }}
      />
      {card.isLoading ? (
        <Typography variant="caption" color="text.secondary">
          Loading the stop card&apos;s suggestions…
        </Typography>
      ) : card.error || !card.data ? (
        <Typography variant="caption" color="text.secondary">
          The stop card&apos;s suggestions could not be loaded. A figure can still be typed.
        </Typography>
      ) : (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
          <Typography variant="caption" color="text.secondary">From the stop card:</Typography>
          {stopSuggestions(card.data).map(({ label, reason, ...suggested }) => (
            <Button
              key={suggested.method}
              title={reason}
              size="small"
              variant={draft.method === suggested.method ? 'contained' : 'outlined'}
              onClick={() => onChange(suggested)}
            >
              {label}
            </Button>
          ))}
        </Stack>
      )}
      <Typography variant="caption" color="text.secondary">
        {fixed
          ? 'The stop is set when the buy fills, and must be below the price it fills at.'
          : 'The stop is set when the buy fills, and trails this far below the highest price since, starting from the fill price.'}{' '}
        The daily check tests it against each closed daily bar and sells the whole position when
        a bar reaches it. A buy placed while the market is closed fills at the open, and its stop
        is live that same day. A buy filled in market hours has its stop checked from the next
        session.
      </Typography>
    </Stack>
  );
}
