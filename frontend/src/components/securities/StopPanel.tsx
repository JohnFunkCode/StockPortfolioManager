/**
 * StopPanel — the stop on one holding, set from the Security page (#431 step
 * 4.5, #221). One panel with two ways in, both writing the same stop:
 *
 * - **From the stop card**: the card's candidates with the card's reasoning,
 *   one of which fills the field, which can then be edited. The stop records
 *   both what was suggested and what was saved.
 * - **Mirror a broker order**: a stop already placed at an outside broker,
 *   with its price and, if the owner gives them, its share count, order type
 *   and brokerage. QuantCore records it and never sends anything to a broker.
 *   Only the real book has a broker.
 *
 * A form, like the Buy dialog's stop field, not a display of analysis: it is
 * not a registered Sidekick component, and the stop card it draws on stays
 * read-only. Every figure is the server's. The card's request is made only
 * when the card path is open, and on the Technical tab it is already cached.
 */
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  MenuItem,
  Paper,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { useStopLoss } from '../../hooks/useSecurities';
import { useHoldingStops, useSetStop } from '../../hooks/useStops';
import { formatCurrency } from '../../utils/formatting';
import { EMPTY_STOP, stopPayload, stopSuggestions } from '../portfolio/stopDraft';
import { BROKER_ORDER_WORDS, stopDetail, trailPercent } from '../portfolio/stopWords';
import type { StopDraft } from '../portfolio/stopDraft';
import type { Book } from '../../api/books';
import type { BrokerOrderType, SetStopPayload, Stop } from '../../api/stops';

type Path = 'card' | 'broker';

/** The order types that suit a kind of stop. */
const ORDER_TYPES: Record<StopDraft['kind'], BrokerOrderType[]> = {
  fixed: ['stop', 'stop_limit'],
  trailing: ['trailing_stop'],
};

interface BrokerDraft {
  quantity: string;
  orderType: BrokerOrderType | '';
  brokerage: string;
}

const EMPTY_BROKER: BrokerDraft = { quantity: '', orderType: '', brokerage: '' };

/** The broker half of a payload, or the reason it can't be sent. */
function brokerPayload(
  draft: BrokerDraft,
): { broker: Pick<SetStopPayload, 'broker_quantity' | 'broker_order_type' | 'brokerage'> } | { error: string } {
  const broker: Pick<SetStopPayload, 'broker_quantity' | 'broker_order_type' | 'brokerage'> = {};
  if (draft.quantity.trim()) {
    const quantity = Number(draft.quantity);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      return { error: "The broker order's share count must be more than zero." };
    }
    broker.broker_quantity = quantity;
  }
  if (draft.orderType) broker.broker_order_type = draft.orderType;
  if (draft.brokerage.trim()) broker.brokerage = draft.brokerage.trim();
  return { broker };
}

interface Props {
  symbol: string;
  /** The real book holds the symbol. */
  heldInReal: boolean;
  /** The owner's open paper books. Whether one holds the symbol is the
   * server's to say. */
  paperBooks: Book[];
}

/** The card's candidates, each with its reasoning, as choices that fill the
 * field. Mounted only on the card path. */
function CardSuggestions({ symbol, draft, onChange }: {
  symbol: string;
  draft: StopDraft;
  onChange: (draft: StopDraft) => void;
}) {
  const card = useStopLoss(symbol, 'full');
  if (card.isLoading) {
    return (
      <Typography variant="caption" color="text.secondary">
        Loading the stop card&apos;s suggestions…
      </Typography>
    );
  }
  if (card.error || !card.data) {
    return (
      <Typography variant="caption" color="text.secondary">
        The stop card&apos;s suggestions could not be loaded. A figure can still be typed.
      </Typography>
    );
  }
  return (
    <Stack spacing={1} data-testid="stop-panel-suggestions">
      {stopSuggestions(card.data).map(({ label, reason, ...suggested }) => {
        const chosen = draft.method === suggested.method;
        return (
          <Box
            key={suggested.method}
            sx={{
              p: 1, borderRadius: 1,
              border: '1px solid',
              borderColor: chosen ? 'primary.main' : 'var(--qc-line)',
              bgcolor: chosen ? 'var(--qc-surface-sunk)' : 'transparent',
            }}
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <Button
                size="small"
                variant={chosen ? 'contained' : 'outlined'}
                onClick={() => onChange(suggested)}
              >
                {label}
              </Button>
              {chosen && <Chip size="small" label="Chosen" />}
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              {reason}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );
}

function CurrentStop({ stop, paper, loading }: { stop: Stop | undefined; paper: boolean; loading: boolean }) {
  if (loading) {
    return (
      <Typography variant="body2" color="text.secondary" data-testid="stop-panel-current">
        Loading this book&apos;s stop…
      </Typography>
    );
  }
  if (!stop) {
    return (
      <Typography variant="body2" color="text.secondary" data-testid="stop-panel-current">
        No stop in this book.
      </Typography>
    );
  }
  const terms = stop.status === 'triggered'
    ? `Reached at ${formatCurrency(stop.trigger_price)}`
    : `${formatCurrency(stop.stop_level)}${stop.kind === 'trailing' ? `, trails ${trailPercent(stop.trail_pct)}` : ''}`;
  return (
    <Box data-testid="stop-panel-current">
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{terms}</Typography>
        <Chip
          size="small"
          label={stop.at_broker ? 'At a broker' : 'Only here'}
          variant="outlined"
        />
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {stopDetail(stop, paper)}
      </Typography>
    </Box>
  );
}

export default function StopPanel({ symbol, heldInReal, paperBooks }: Props) {
  // null is the real book.
  const choices: Array<{ id: number | null; name: string }> = [
    ...(heldInReal ? [{ id: null, name: 'Real' }] : []),
    ...paperBooks.map((book) => ({ id: book.book_id, name: book.name })),
  ];
  const [bookId, setBookId] = useState<number | null>(choices[0]?.id ?? null);
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState<Path>('card');
  const [draft, setDraft] = useState<StopDraft>(EMPTY_STOP);
  const [broker, setBroker] = useState<BrokerDraft>(EMPTY_BROKER);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');

  const paper = bookId !== null;
  const { data: held, isLoading: heldLoading } = useHoldingStops(bookId, choices.length > 0);
  const current = held?.stops.find((stop) => stop.symbol === symbol);
  const setStop = useSetStop();

  if (choices.length === 0) return null;

  const fixed = draft.kind === 'fixed';
  const mirroring = path === 'broker' && !paper;

  const reset = () => {
    setDraft(EMPTY_STOP);
    setBroker(EMPTY_BROKER);
    setError('');
  };

  const chooseBook = (id: number | null) => {
    setBookId(id);
    // A paper book has no broker.
    if (id !== null) setPath('card');
    setSaved('');
    setError('');
  };

  const save = () => {
    setError('');
    const terms = stopPayload(mirroring ? { ...draft, method: 'manual', proposed: null } : draft);
    if ('error' in terms) {
      setError(terms.error);
      return;
    }
    let payload: SetStopPayload = { symbol, ...terms.stop, ...(paper ? { book_id: bookId } : {}) };
    if (mirroring) {
      const details = brokerPayload(broker);
      if ('error' in details) {
        setError(details.error);
        return;
      }
      payload = { ...payload, at_broker: true, ...details.broker };
    }
    setStop.mutate(payload, {
      onSuccess: (stop) => {
        setSaved(
          `Stop set at ${formatCurrency(stop.stop_level)}` +
          (stop.replaced_stop_id != null ? ', in place of the one before.' : '.'),
        );
        setOpen(false);
        reset();
      },
      // The server's refusal says why: a level at or above the last close, a
      // symbol the book doesn't hold, more shares than it holds.
      onError: (e) => setError((e as Error).message),
    });
  };

  const activeCurrent = current?.status === 'active' ? current : undefined;

  return (
    <Paper sx={{ p: 2 }} data-testid="stop-panel">
      <Typography variant="subtitle1" sx={{ mb: 1 }}>Your stop on {symbol}</Typography>
      <Stack spacing={1.5}>
        {choices.length > 1 && (
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap aria-label="Book">
            {choices.map((choice) => (
              <Chip
                key={choice.id ?? 'real'}
                label={choice.name}
                color={choice.id === bookId ? 'primary' : 'default'}
                variant={choice.id === bookId ? 'filled' : 'outlined'}
                onClick={() => chooseBook(choice.id)}
                aria-pressed={choice.id === bookId}
              />
            ))}
          </Stack>
        )}

        <CurrentStop stop={current} paper={paper} loading={heldLoading} />
        {saved && <Alert severity="success">{saved}</Alert>}

        {!open ? (
          <Box>
            <Button
              size="small"
              variant="outlined"
              onClick={() => { reset(); setSaved(''); setOpen(true); }}
            >
              {activeCurrent ? 'Replace this stop' : 'Set a stop'}
            </Button>
          </Box>
        ) : (
          <Stack spacing={1.5} data-testid="stop-panel-form">
            <ToggleButtonGroup
              size="small"
              exclusive
              value={mirroring ? 'broker' : 'card'}
              onChange={(_, next: Path | null) => {
                if (!next) return;
                setPath(next);
                reset();
              }}
              aria-label="How the stop is set"
            >
              <ToggleButton value="card">From the stop card</ToggleButton>
              <ToggleButton value="broker" disabled={paper}>Mirror a broker order</ToggleButton>
            </ToggleButtonGroup>
            {paper && (
              <Typography variant="caption" color="text.secondary">
                A paper book has no broker, so its stops are set here only.
              </Typography>
            )}

            {activeCurrent && (
              <Alert severity="warning" data-testid="stop-panel-replaces">
                The stop set here replaces the one at {formatCurrency(activeCurrent.stop_level)}
                {activeCurrent.at_broker
                  ? '. Change or cancel that order at your broker too: QuantCore never sends anything there.'
                  : '.'}
              </Alert>
            )}

            {!mirroring && <CardSuggestions symbol={symbol} draft={draft} onChange={setDraft} />}

            <ToggleButtonGroup
              size="small"
              exclusive
              value={draft.kind}
              onChange={(_, next: StopDraft['kind'] | null) => {
                // A new kind is a new stop: the old figure means nothing in it.
                if (!next) return;
                setDraft({ ...EMPTY_STOP, kind: next });
                setBroker((b) => ({ ...b, orderType: '' }));
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
              onChange={(e) => setDraft({ ...draft, value: e.target.value })}
              inputProps={{ min: 0, step: fixed ? 0.01 : 0.1 }}
              helperText={
                !mirroring && draft.method !== 'manual'
                  ? "From the stop card. Edit it to override: the card's figure is kept beside yours."
                  : undefined
              }
            />

            {mirroring && (
              <Stack spacing={1.5} data-testid="stop-panel-broker">
                <TextField
                  label="Shares (optional)"
                  size="small"
                  type="number"
                  value={broker.quantity}
                  onChange={(e) => setBroker({ ...broker, quantity: e.target.value })}
                  inputProps={{ min: 0, step: 'any' }}
                />
                <TextField
                  select
                  label="Order type (optional)"
                  size="small"
                  value={broker.orderType}
                  onChange={(e) => setBroker({ ...broker, orderType: e.target.value as BrokerOrderType | '' })}
                >
                  <MenuItem value="">Not given</MenuItem>
                  {ORDER_TYPES[draft.kind].map((type) => (
                    <MenuItem key={type} value={type}>{BROKER_ORDER_WORDS[type]}</MenuItem>
                  ))}
                </TextField>
                <TextField
                  label="Brokerage (optional)"
                  size="small"
                  value={broker.brokerage}
                  onChange={(e) => setBroker({ ...broker, brokerage: e.target.value })}
                  inputProps={{ maxLength: 60 }}
                />
              </Stack>
            )}

            <Typography variant="caption" color="text.secondary">
              {mirroring
                ? 'This records a stop you already placed at a broker. Nothing is sent to the broker. '
                : ''}
              The stop must be below the last stored close
              {fixed ? '' : ', starts from that close, and trails this far below the highest price since'}.
              The daily check tests it against each closed daily bar from the next session
              {paper
                ? ', and sells the whole position when a bar reaches it.'
                : ', and alerts when a bar reaches it or a close comes near it. Nothing is sold.'}
            </Typography>

            {error && <Alert severity="error">{error}</Alert>}

            <Stack direction="row" spacing={1}>
              <Button size="small" onClick={() => { setOpen(false); reset(); }}>Cancel</Button>
              <Button
                size="small"
                variant="contained"
                onClick={save}
                disabled={setStop.isPending || !draft.value.trim()}
              >
                {setStop.isPending ? 'Saving…' : 'Save stop'}
              </Button>
            </Stack>
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
