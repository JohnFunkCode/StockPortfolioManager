/**
 * CashLedger — the cash of one book (#431 step 3.1): the balance, the opening
 * balance it runs from, and every deposit, withdrawal, buy and sell since.
 *
 * One request, `GET /api/portfolio/cash`, feeds everything here. The balance,
 * each row's running balance and the totals all arrive computed (Rule 8.4):
 * the only work in this file is formatting.
 *
 * Self-contained: `bookId` names a book and null is the real book. The
 * Portfolio page mounts it as its Cash and orders section, and the Sidekick's
 * `cash_ledger` card mounts it `readOnly`. The forms show only for the real
 * book: a paper book's cash moves through its orders, which a later step adds
 * to this section.
 */
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  MenuItem,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import {
  useAddCashEntry,
  useCashLedger,
  useDeleteCashEntry,
  useSetOpeningBalance,
} from '../../hooks/useCash';
import { formatCurrency, formatDate, formatShares } from '../../utils/formatting';
import type { CashEntry, CashLedger as Ledger } from '../../api/cash';

interface Props {
  /** One of the caller's books; null or absent is the real book. */
  bookId?: number | null;
  title?: string;
  /** No forms and no delete, whatever the book is. */
  readOnly?: boolean;
}

/** How many entries show before "Show all entries". */
export const RECENT_ENTRIES = 5;

type FormMode = 'opening' | 'entry' | null;

const KIND_LABELS: Record<string, string> = {
  opening_balance: 'Opening balance',
  deposit: 'Deposit',
  withdrawal: 'Withdrawal',
};

const NO_FX_HINT =
  'No exchange rate is recorded for this trade, so it has no dollar amount and is not in the balance.';

function amountColor(value: number | null): string | undefined {
  if (value == null || value === 0) return undefined;
  return value > 0 ? '#10b981' : '#ef4444';
}

/** "Buy 5 INTC @ $30.00", in the lot's own currency. */
function describe(entry: CashEntry): string {
  const label = KIND_LABELS[entry.kind];
  if (label) return label;
  const verb = entry.kind === 'buy' ? 'Buy' : 'Sell';
  if (entry.symbol == null) return verb;
  return `${verb} ${formatShares(entry.shares)} ${entry.symbol} @ ${formatCurrency(entry.price, entry.currency)}`;
}

function Stat({ label, value, detail, color }: { label: string; value: string; detail?: string; color?: string }) {
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" sx={color ? { color } : undefined}>{value}</Typography>
      {detail && <Typography variant="caption" color="text.secondary">{detail}</Typography>}
    </Stack>
  );
}

/** Today as the date input writes it, in the browser's own time zone. */
function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** The opening-balance form, or the deposit and withdrawal form. The server
 * is the check; this only stops a blank or non-numeric amount going out. */
function CashForm({ mode, ledger, onDone }: { mode: 'opening' | 'entry'; ledger: Ledger; onDone: () => void }) {
  const opening = mode === 'opening';
  const [kind, setKind] = useState<'deposit' | 'withdrawal'>('deposit');
  const [date, setDate] = useState(opening ? (ledger.opening_date ?? today()) : today());
  const [amount, setAmount] = useState(
    opening && ledger.opening_balance != null ? String(ledger.opening_balance) : '',
  );
  const [notes, setNotes] = useState('');
  const [problem, setProblem] = useState('');
  const setOpening = useSetOpeningBalance();
  const addEntry = useAddCashEntry();
  const mutation = opening ? setOpening : addEntry;

  const submit = () => {
    const value = amount.trim() ? Number(amount) : NaN;
    if (!Number.isFinite(value) || (!opening && value <= 0)) {
      setProblem(opening ? 'Enter the cash as a number.' : 'Enter an amount above zero.');
      return;
    }
    if (!date) {
      setProblem('Pick a date.');
      return;
    }
    setProblem('');
    const shared = { date, amount: value, ...(notes.trim() ? { notes: notes.trim() } : {}) };
    if (opening) setOpening.mutate(shared, { onSuccess: onDone });
    else addEntry.mutate({ kind, ...shared }, { onSuccess: onDone });
  };

  return (
    <Paper sx={{ p: 2, mb: 2 }} data-testid={`cash-form-${mode}`}>
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          {opening
            ? 'The cash this book held at the start of the date. Trades and transfers dated on or after it count; anything earlier is left out.'
            : 'Cash paid into or taken out of the brokerage account. Buys and sells are added on their own.'}
        </Typography>
        <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
          {!opening && (
            <TextField
              select
              label="Kind"
              size="small"
              value={kind}
              onChange={(e) => setKind(e.target.value as 'deposit' | 'withdrawal')}
              sx={{ minWidth: 140 }}
            >
              <MenuItem value="deposit">Deposit</MenuItem>
              <MenuItem value="withdrawal">Withdrawal</MenuItem>
            </TextField>
          )}
          <TextField
            label="Date"
            size="small"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            InputLabelProps={{ shrink: true }}
            inputProps={{ max: today() }}
          />
          <TextField
            label={opening ? 'Cash held' : 'Amount'}
            size="small"
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            inputProps={{ step: 'any' }}
          />
          <TextField
            label="Note"
            size="small"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            inputProps={{ maxLength: 500 }}
            sx={{ flexGrow: 1, minWidth: 160 }}
          />
        </Stack>
        {(problem || mutation.error) && (
          <Alert severity="error">{problem || (mutation.error as Error).message}</Alert>
        )}
        <Stack direction="row" spacing={1}>
          <Button variant="contained" size="small" onClick={submit} disabled={mutation.isPending}>
            {opening ? 'Save opening balance' : 'Save entry'}
          </Button>
          <Button size="small" color="inherit" onClick={onDone}>Cancel</Button>
        </Stack>
      </Stack>
    </Paper>
  );
}

function EntryRow({ entry, editable }: { entry: CashEntry; editable: boolean }) {
  const remove = useDeleteCashEntry();
  const manual = entry.kind === 'deposit' || entry.kind === 'withdrawal';
  return (
    <TableRow hover data-testid={`cash-entry-${entry.entry_id}`}>
      <TableCell>{formatDate(entry.entry_date)}</TableCell>
      <TableCell>
        {describe(entry)}
        {entry.notes && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {entry.notes}
          </Typography>
        )}
      </TableCell>
      <TableCell align="right" sx={{ color: amountColor(entry.amount) }}>
        {entry.fx_missing ? (
          <Tooltip title={NO_FX_HINT}>
            <Chip
              size="small"
              label="no FX rate"
              sx={{ bgcolor: 'var(--qc-tint-amber)', color: 'var(--qc-ink-amber)' }}
            />
          </Tooltip>
        ) : (
          formatCurrency(entry.amount)
        )}
      </TableCell>
      <TableCell align="right">{formatCurrency(entry.balance_after)}</TableCell>
      {editable && (
        <TableCell align="right" sx={{ width: 48 }}>
          {manual && (
            <IconButton
              size="small"
              aria-label={`Delete ${KIND_LABELS[entry.kind].toLowerCase()} of ${formatDate(entry.entry_date)}`}
              disabled={remove.isPending}
              onClick={() => remove.mutate(entry.entry_id)}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          )}
        </TableCell>
      )}
    </TableRow>
  );
}

/** "3 entries", "1 entry". */
function entries(n: number): string {
  return `${n} ${n === 1 ? 'entry' : 'entries'}`;
}

function LedgerBody({ ledger, readOnly }: { ledger: Ledger; readOnly: boolean }) {
  const [form, setForm] = useState<FormMode>(null);
  const [showAll, setShowAll] = useState(false);
  // What the owner types goes to the real book, so the forms show only there.
  const editable = !readOnly && ledger.book_kind === 'real';
  const closeForm = () => setForm(null);

  if (ledger.balance == null) {
    return (
      <>
        <Alert severity="info" sx={{ mb: 2 }}>
          {editable
            ? 'This book has no opening balance yet. Enter the cash it held on a date to start its ledger.'
            : 'This book has no opening balance yet, so it has no cash balance.'}
          {ledger.earlier_entry_count > 0 &&
            ` ${entries(ledger.earlier_entry_count)} from its lots and sales will count once it has one.`}
        </Alert>
        {editable &&
          (form === 'opening' ? (
            <CashForm mode="opening" ledger={ledger} onDone={closeForm} />
          ) : (
            <Button variant="outlined" size="small" onClick={() => setForm('opening')}>
              Set opening balance
            </Button>
          ))}
      </>
    );
  }

  const shown = showAll ? ledger.entries : ledger.entries.slice(0, RECENT_ENTRIES);
  return (
    <>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
          <Stat
            label="Cash"
            value={formatCurrency(ledger.balance)}
            color={ledger.negative ? '#ef4444' : undefined}
          />
          <Stat
            label="Opening balance"
            value={formatCurrency(ledger.opening_balance)}
            detail={`at the start of ${formatDate(ledger.opening_date)}`}
          />
          <Stat label="Deposits" value={formatCurrency(ledger.deposits)} />
          <Stat label="Withdrawals" value={formatCurrency(ledger.withdrawals)} />
          <Stat label="Bought" value={formatCurrency(ledger.bought)} />
          <Stat label="Sold" value={formatCurrency(ledger.sold)} />
        </Stack>
      </Paper>

      {ledger.negative && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Cash is below zero. A deposit is probably missing, or the opening balance is too low.
        </Alert>
      )}
      {ledger.fx_missing_count > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {entries(ledger.fx_missing_count)} with no exchange rate{' '}
          {ledger.fx_missing_count === 1 ? 'is' : 'are'} not in the balance.
        </Alert>
      )}

      {editable && (
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <Button variant="outlined" size="small" onClick={() => setForm('entry')}>
            Add deposit or withdrawal
          </Button>
          <Button size="small" color="inherit" onClick={() => setForm('opening')}>
            Change opening balance
          </Button>
        </Stack>
      )}
      {/* Keyed by mode, so switching forms starts the new one clean. */}
      {editable && form && <CashForm key={form} mode={form} ledger={ledger} onDone={closeForm} />}

      <Paper sx={{ overflowX: 'auto' }}>
        <Table size="small" data-testid="cash-table">
          <TableHead>
            <TableRow>
              <TableCell>Date</TableCell>
              <TableCell>Entry</TableCell>
              <TableCell align="right">Amount</TableCell>
              <TableCell align="right">Balance</TableCell>
              {editable && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {shown.map((entry) => (
              <EntryRow key={entry.entry_id} entry={entry} editable={editable} />
            ))}
          </TableBody>
        </Table>
      </Paper>
      {ledger.entries.length > RECENT_ENTRIES && (
        <Button size="small" sx={{ mt: 1 }} onClick={() => setShowAll((all) => !all)}>
          {showAll ? 'Show the latest entries' : `Show all ${entries(ledger.entries.length)}`}
        </Button>
      )}

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        In US dollars, newest first. Buys and sells exclude fees.
        {ledger.earlier_entry_count > 0 &&
          ` ${entries(ledger.earlier_entry_count)} dated before the opening balance ${
            ledger.earlier_entry_count === 1 ? 'is' : 'are'
          } not shown.`}
      </Typography>
    </>
  );
}

export default function CashLedger({ bookId = null, title = 'Cash and orders', readOnly = false }: Props) {
  const { data, isLoading, error } = useCashLedger(bookId);

  return (
    <Box data-testid="cash-ledger">
      <Typography variant="h5" component="h2" sx={{ mb: 2 }}>{title}</Typography>
      {isLoading ? (
        <Stack direction="row" spacing={1} alignItems="center">
          <CircularProgress size={16} />
          <Typography variant="body2" color="text.secondary">Loading cash…</Typography>
        </Stack>
      ) : error || !data ? (
        <Alert severity="error">Couldn't load cash: {(error as Error | null)?.message ?? 'no data'}</Alert>
      ) : (
        <LedgerBody ledger={data} readOnly={readOnly} />
      )}
    </Box>
  );
}
