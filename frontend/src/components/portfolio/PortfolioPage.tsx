import { Fragment, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RefreshIcon from '@mui/icons-material/Refresh';
import CloseIcon from '@mui/icons-material/Close';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight';
import { useRealized, useSymbolRows } from '../../hooks/usePortfolio';
import { useBookPerformance, useBooks } from '../../hooks/useBooks';
import { useCashLedger } from '../../hooks/useCash';
import { useCancelStop, useHoldingStops } from '../../hooks/useStops';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import OneDayChange, { compareOneDay } from '../common/OneDayChange';
import EmptyPortfolio from './EmptyPortfolio';
import PortfolioSummary from './PortfolioSummary';
import PortfolioAllocationChart from './PortfolioAllocationChart';
import LotRow from './LotRow';
import AddLotDialog from './AddLotDialog';
import BookSelector from './BookSelector';
import PaperBookBanner from './PaperBookBanner';
import BookSummaryStrip from './BookSummaryStrip';
import RealizedPnl from './RealizedPnl';
import BookPerformance from './BookPerformance';
import CashLedger from './CashLedger';
import PaperOrderDialog from './PaperOrderDialog';
import SeedEmptyBook from './SeedEmptyBook';
import StopCell from './StopCell';
import ConfirmDialog from '../common/ConfirmDialog';
import CreatePlanDialog from '../plans/CreatePlanDialog';
import { formatCurrency, formatDollarsPerDay, formatPercentRaw } from '../../utils/formatting';
import type { Lot, SymbolRow } from '../../api/portfolioTypes';
import type { Stop } from '../../api/stops';

type SortKey =
  | 'symbol'
  | 'total_investment'
  | 'total_current_value'
  | 'total_gain_loss'
  | 'total_gain_loss_pct'
  | 'total_dollars_per_day'
  | 'return_1d_display'
  | 'return_5d'
  | 'return_30d'
  | 'return_60d'
  | 'return_ytd'
  | 'return_1y';

// The windows are easy to misread, so each header says what it measures.
const BARS = 'Trading days, not calendar days';
const RETURN_COLUMNS: Array<{ key: SortKey; label: string; hint: string }> = [
  { key: 'return_5d', label: '5d', hint: `Last 5 bars. ${BARS}.` },
  { key: 'return_30d', label: '30d', hint: `Last 30 bars, about 6 calendar weeks. ${BARS}.` },
  { key: 'return_60d', label: '60d', hint: `Last 60 bars, about 12 calendar weeks. ${BARS}.` },
  { key: 'return_ytd', label: 'YTD', hint: 'Since the first trading day of this calendar year.' },
  { key: 'return_1y', label: '1y', hint: 'Since the same calendar date a year ago.' },
];

const COLUMNS: Array<{ key: SortKey; label: string; align?: 'right'; hint?: string }> = [
  { key: 'symbol', label: 'Symbol' },
  { key: 'total_investment', label: 'Investment', align: 'right' },
  { key: 'total_current_value', label: 'Current Value', align: 'right' },
  { key: 'total_gain_loss', label: 'Gain/Loss $', align: 'right' },
  { key: 'total_gain_loss_pct', label: 'Gain/Loss %', align: 'right' },
  { key: 'total_dollars_per_day', label: '$/Day', align: 'right' },
  {
    key: 'return_1d_display',
    label: '1d',
    align: 'right',
    hint: "Today's regular-session change. A row marked stale has no live quote and shows the last cached daily change.",
  },
  ...RETURN_COLUMNS.map((col) => ({ ...col, align: 'right' as const })),
];

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

const HEDGE_COLORS: Record<string, { bg: string; color: string }> = {
  buy_on_rally: { bg: 'var(--qc-tint-green)', color: 'var(--qc-ink-green)' },
};

/** The harvest ladder (issue #147 Part H6). A chip when one is running, a
 * quiet invitation when none is — deliberately not the next rung's price,
 * which would be a second number competing with the returns. Row-bound: it
 * shows only what its row already holds. */
function PlanCell({
  planId,
  onOpen,
  onCreate,
}: {
  planId: number | null;
  onOpen: (planId: number) => void;
  onCreate: () => void;
}) {
  return (
    <TableCell>
      {planId != null ? (
        <Chip
          size="small"
          label="Plan"
          clickable
          onClick={() => onOpen(planId)}
          sx={{ bgcolor: 'var(--qc-tint-blue)', color: 'var(--qc-ink-blue)' }}
        />
      ) : (
        <Button
          size="small"
          color="inherit"
          sx={{ color: 'text.secondary', textTransform: 'none', minWidth: 0 }}
          onClick={onCreate}
        >
          Create plan
        </Button>
      )}
    </TableCell>
  );
}

/** One symbol's open lots. `readOnly` is a paper book (#431): the Actions
 * column goes, because the system sets every paper fill. `stop` is the
 * symbol's stop: it is set on the symbol, so every lot is under it. */
function LotsTable({ lots, readOnly, stop }: { lots: Lot[]; readOnly: boolean; stop?: Stop }) {
  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell />
          <TableCell>Trade Date</TableCell>
          <TableCell>Price Paid</TableCell>
          <TableCell>Shares</TableCell>
          <TableCell>Gain/Loss %</TableCell>
          <TableCell>Gain/Loss $</TableCell>
          <TableCell>Days Held</TableCell>
          <TableCell>$/Day</TableCell>
          <TableCell>Account</TableCell>
          <TableCell>Stop</TableCell>
          {!readOnly && <TableCell align="right">Actions</TableCell>}
        </TableRow>
      </TableHead>
      <TableBody>
        {lots.map((lot) => (
          <LotRow key={lot.lot_id} lot={lot} readOnly={readOnly} stop={stop} />
        ))}
      </TableBody>
    </Table>
  );
}

/** The paper book named by `?book=<id>`, or null for the real book. */
function bookIdFrom(params: URLSearchParams): number | null {
  const raw = params.get('book');
  return raw && /^\d+$/.test(raw) ? Number(raw) : null;
}

export default function PortfolioPage() {
  const navigate = useNavigate();
  // The selected book lives in the URL (#431), so a reload keeps it. No
  // `book` means the real book, and the page then sends no book id at all.
  const [searchParams, setSearchParams] = useSearchParams();
  const bookId = bookIdFrom(searchParams);
  const selectBook = (id: number | null) =>
    setSearchParams(id === null ? {} : { book: String(id) }, { replace: true });
  const { data: booksData } = useBooks();
  const book = booksData?.books.find((b) => b.book_id === bookId);
  // A named book is editable only once the list says it is the real one, so
  // nothing is editable while the list loads or after it fails.
  const readOnly = bookId !== null && book?.kind !== 'real';
  // It is called paper only once the list has loaded: a list that is loading
  // or failed can't say what the book is. A book missing from a loaded list
  // (an archived one, opened by id) is paper.
  const isPaper = readOnly && booksData !== undefined;

  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useSymbolRows(false, bookId);
  // The strip's realized figure is always the last year, whatever period the
  // Realized section below is showing. That section opens on Year, so the two
  // share one request until its period is changed.
  const { data: realizedYear } = useRealized('year', bookId);
  // The same query the Cash section below makes, so this is not a second request.
  const { data: cash } = useCashLedger(bookId);
  // The strip's return is always the last year; the Performance section opens
  // on Year, so the two share one request until its period is changed.
  const { data: performanceYear } = useBookPerformance('year', bookId);

  const [sortKey, setSortKey] = useState<SortKey>('symbol');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [addOpen, setAddOpen] = useState(false);
  // The symbol whose "Create plan" affordance was clicked (issue #147 Part H6);
  // null keeps the dialog shut.
  const [planSymbol, setPlanSymbol] = useState<string | null>(null);

  // A paper book that is still open trades through orders (#431 step 3.5).
  const tradable = isPaper && book != null && book.archived_at == null;
  // The paper position whose Sell was clicked; null keeps the dialog shut.
  const [selling, setSelling] = useState<SymbolRow | null>(null);

  const rows = data?.symbols ?? [];

  // The book's stops are their own request (#431 step 4.3): the holdings
  // never wait for them, and a failed read leaves the column blank.
  // The server picks the one stop each holding shows (decision 66).
  const { data: stopsData } = useHoldingStops(bookId);
  const stops = useMemo<Record<string, Stop>>(
    () => Object.fromEntries((stopsData?.stops ?? []).map((stop) => [stop.symbol, stop])),
    [stopsData],
  );
  // The stop whose Cancel was clicked; null keeps the confirmation shut.
  const [cancelling, setCancelling] = useState<Stop | null>(null);
  const cancelStop = useCancelStop(bookId);
  const closeCancel = () => {
    cancelStop.reset();
    setCancelling(null);
  };

  const sorted = useMemo<SymbolRow[]>(() => {
    const copy = [...rows];
    if (sortKey === 'return_1d_display') {
      // Stale rows stay last in both directions, so the direction is applied
      // inside the comparator and the reverse below is skipped.
      return copy.sort((a, b) => compareOneDay(a, b, sortDir));
    }
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv));
      }
      return (av as number) - (bv as number);
    });
    if (sortDir === 'desc') copy.reverse();
    return copy;
  }, [rows, sortKey, sortDir]);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const toggleExpanded = (symbol: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  };

  // Lot columns, plus Actions for a book that can be edited by hand.
  // A tradable paper book has a Sell column where the real book has Plan.
  // Every book has the Stop column.
  const lotColSpan = COLUMNS.length + (readOnly && !tradable ? 3 : 4);

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h4">Portfolio</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          {dataUpdatedAt > 0 && (
            <Typography variant="caption" color="text.secondary">
              as of {new Date(dataUpdatedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
            </Typography>
          )}
          <Button
            size="small"
            startIcon={isFetching ? <CircularProgress size={14} /> : <RefreshIcon />}
            onClick={() => refetch()}
            disabled={isFetching}
          >
            Refresh
          </Button>
          {/* Add Lot is for the real book only: the system sets every paper
              fill (#431). */}
          {!readOnly && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
              Add Lot
            </Button>
          )}
        </Stack>
      </Stack>

      {/* The selector and banner sit outside the loading and error states, so
          a book that fails to load can still be left. */}
      <BookSelector selectedBookId={bookId} onSelect={selectBook} />
      {isPaper && <PaperBookBanner book={book} onArchived={() => selectBook(null)} />}

      <BookSummaryStrip
        holdings={data?.totals}
        realizedYear={realizedYear?.totals}
        cash={cash?.balance}
        performanceYear={performanceYear}
      />

      {/* Each section loads and fails on its own: Holdings never waits for
          the sections under it. */}
      <Typography id="holdings" variant="h5" component="h2" sx={{ mb: 2, scrollMarginTop: 16 }}>
        Holdings
      </Typography>
      {isLoading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorAlert message={(error as Error).message} />
      ) : rows.length === 0 ? (
        <EmptyPortfolio paper={isPaper}>
          {tradable && bookId !== null && <SeedEmptyBook bookId={bookId} />}
        </EmptyPortfolio>
      ) : (
        <>
          {data?.totals && <PortfolioSummary totals={data.totals} />}

          {/* Reuses the rows already fetched above — no second request. The
              chart draws nothing when no symbol has a price yet, so skip the
              Paper too rather than leaving an empty panel on the page. */}
          {rows.some((row) => row.bar_base != null) && (
            <Paper sx={{ p: 2, mb: 2 }}>
              <PortfolioAllocationChart rows={rows} />
            </Paper>
          )}

          <Paper sx={{ overflowX: 'auto' }}>
            <Table size="small" data-testid="holdings-table">
              <TableHead>
                <TableRow>
                  <TableCell />
                  {COLUMNS.map((col) => (
                    <TableCell key={col.key} align={col.align}>
                      <Tooltip title={col.hint ?? ''} disableHoverListener={!col.hint}>
                        <TableSortLabel
                          active={sortKey === col.key}
                          direction={sortKey === col.key ? sortDir : 'asc'}
                          onClick={() => handleSort(col.key)}
                        >
                          {col.label}
                        </TableSortLabel>
                      </Tooltip>
                    </TableCell>
                  ))}
                  <TableCell>MM Hedge Bias</TableCell>
                  <TableCell>
                    <Tooltip title="The stop set on this holding. The daily check tests it against each closed daily bar: a real book's stop alerts, a paper book's sells the position.">
                      <span>Stop</span>
                    </Tooltip>
                  </TableCell>
                  {/* Harvest plans belong to real holdings. */}
                  {!readOnly && <TableCell>Plan</TableCell>}
                  {tradable && <TableCell />}
                </TableRow>
              </TableHead>
              <TableBody>
                {sorted.map((row) => {
                  const isOpen = expanded.has(row.symbol);
                  const hedge = row.mm_hedge_bias
                    ? (HEDGE_COLORS[row.mm_hedge_bias] ?? { bg: 'var(--qc-tint-red)', color: 'var(--qc-ink-red)' })
                    : null;
                  return (
                    <Fragment key={row.symbol}>
                      <TableRow hover sx={{ cursor: 'pointer' }}>
                        <TableCell>
                          <IconButton size="small" onClick={() => toggleExpanded(row.symbol)}>
                            {isOpen ? <KeyboardArrowDownIcon fontSize="small" /> : <KeyboardArrowRightIcon fontSize="small" />}
                          </IconButton>
                        </TableCell>
                        <TableCell onClick={() => navigate(`/securities/${row.symbol}`)}>
                          <Typography variant="body2" sx={{ fontWeight: 600, color: 'secondary.main' }}>
                            {row.symbol}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">{formatCurrency(row.total_investment)}</TableCell>
                        <TableCell align="right">{formatCurrency(row.total_current_value)}</TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.total_gain_loss) }}>
                          {formatCurrency(row.total_gain_loss)}
                        </TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.total_gain_loss_pct) }}>
                          {formatPercentRaw(row.total_gain_loss_pct)}
                        </TableCell>
                        <TableCell align="right">{formatDollarsPerDay(row.total_dollars_per_day)}</TableCell>
                        <TableCell align="right" data-testid={`one-day-${row.symbol}`}>
                          <OneDayChange row={row} />
                        </TableCell>
                        {RETURN_COLUMNS.map((col) => {
                          const value = row[col.key] as number | null;
                          return (
                            <TableCell key={col.key} align="right" sx={{ color: gainColor(value) }}>
                              {formatPercentRaw(value)}
                            </TableCell>
                          );
                        })}
                        <TableCell>
                          {hedge ? (
                            <Chip
                              size="small"
                              label={row.mm_hedge_bias}
                              sx={{ bgcolor: hedge.bg, color: hedge.color }}
                            />
                          ) : (
                            '—'
                          )}
                        </TableCell>
                        <TableCell data-testid={`stop-${row.symbol}`} sx={{ whiteSpace: 'nowrap' }}>
                          <StopCell stop={stops[row.symbol]} paper={isPaper} />
                          {/* A live stop can be taken off, in any book (decision 67). */}
                          {stops[row.symbol]?.status === 'active' && (
                            <Tooltip title="Take this stop off">
                              <IconButton
                                size="small"
                                aria-label={`Take the stop off ${row.symbol}`}
                                onClick={() => setCancelling(stops[row.symbol])}
                                sx={{ ml: 0.5 }}
                              >
                                <CloseIcon fontSize="inherit" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </TableCell>
                        {!readOnly && (
                          <PlanCell
                            planId={row.active_plan_id}
                            onOpen={(id) => navigate(`/plans/${id}`)}
                            onCreate={() => setPlanSymbol(row.symbol)}
                          />
                        )}
                        {tradable && (
                          <TableCell align="right">
                            <Button
                              size="small"
                              color="inherit"
                              aria-label={`Sell ${row.symbol}`}
                              onClick={() => setSelling(row)}
                            >
                              Sell
                            </Button>
                          </TableCell>
                        )}
                      </TableRow>
                      <TableRow>
                        <TableCell colSpan={lotColSpan} sx={{ p: 0, borderBottom: isOpen ? undefined : 'none' }}>
                          <Collapse in={isOpen} unmountOnExit>
                            <Box sx={{ pl: 6, pr: 2, py: 1 }}>
                              <LotsTable lots={row.lots} readOnly={readOnly} stop={stops[row.symbol]} />
                            </Box>
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </Paper>
        </>
      )}

      {/* A book with no open lots can still have sales, so this section does
          not depend on the holdings above. */}
      <Box id="realized" sx={{ mt: 4, scrollMarginTop: 16 }}>
        <RealizedPnl bookId={bookId} />
      </Box>

      <Box id="performance" sx={{ mt: 4, scrollMarginTop: 16 }}>
        <BookPerformance bookId={bookId} />
      </Box>

      {/* A paper book, or one the list can't vouch for, gets no forms. */}
      <Box id="cash" sx={{ mt: 4, scrollMarginTop: 16 }}>
        {/* A pending order can be withdrawn from a paper book that is still
            open. `readOnly` is about what the owner types: lots and cash. */}
        <CashLedger
          bookId={bookId}
          readOnly={readOnly}
          canCancelOrders={tradable}
        />
      </Box>

      <AddLotDialog open={addOpen} onClose={() => setAddOpen(false)} />

      <ConfirmDialog
        open={cancelling !== null}
        title={`Take the stop off ${cancelling?.symbol ?? ''}`}
        message={
          cancelStop.error
            ? `The stop was not taken off: ${(cancelStop.error as Error).message}`
            : `Take the stop at ${formatCurrency(cancelling?.stop_level)} off ${cancelling?.symbol ?? ''}? ` +
              'The holding will then have no stop. The stop is kept in the record as cancelled.'
        }
        confirmLabel="Take it off"
        loading={cancelStop.isPending}
        onConfirm={() => cancelling && cancelStop.mutate(cancelling.stop_id, { onSuccess: closeCancel })}
        onCancel={closeCancel}
      />

      {selling && book && (
        <PaperOrderDialog
          symbol={selling.symbol}
          side="sell"
          books={[book]}
          heldShares={selling.total_quantity}
          unpriced={selling.total_current_value == null}
          onClose={() => setSelling(null)}
        />
      )}

      <CreatePlanDialog
        open={planSymbol !== null}
        initialSymbol={planSymbol ?? undefined}
        onClose={() => setPlanSymbol(null)}
        onCreated={(id) => navigate(`/plans/${id}`)}
      />
    </Box>
  );
}
