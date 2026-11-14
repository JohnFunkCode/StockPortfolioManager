/**
 * RealizedPnl — what the closed lots of one book made or lost (#235).
 *
 * One request, `GET /api/portfolio/realized`, feeds the summary, the chart,
 * the table and every expanded row's sales. The period pulldown changes the
 * request, never a client-side filter, so the totals are always the server's
 * sums over the sales shown. No figure is computed here (Rule 8.4): the only
 * work in this file is sorting and formatting.
 *
 * Self-contained: `bookId` names a book and null is the real book. The
 * Portfolio page mounts it as its Realized section, and the Sidekick's
 * `realized_pnl` card mounts it with no props at all.
 */
import { Fragment, useMemo, useState } from 'react';
import {
  Alert,
  Box,
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
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight';
import { useRealized } from '../../hooks/usePortfolio';
import PortfolioAllocationChart from './PortfolioAllocationChart';
import { formatCurrency, formatDate, formatPercentRaw, formatShares } from '../../utils/formatting';
import type {
  RealizedPeriod,
  RealizedResponse,
  RealizedSale,
  RealizedSymbolRow,
} from '../../api/portfolioTypes';

interface Props {
  /** One of the caller's books; null or absent is the real book. */
  bookId?: number | null;
  title?: string;
}

const PERIODS: Array<{ value: RealizedPeriod; label: string; empty: string }> = [
  { value: 'month', label: 'Month', empty: 'in the last 30 days' },
  { value: 'quarter', label: 'Quarter', empty: 'in the last 90 days' },
  { value: 'year', label: 'Year', empty: 'in the last 365 days' },
  { value: 'all', label: 'All', empty: '' },
];

type SortKey = 'symbol' | 'cost_basis' | 'proceeds' | 'realized_pnl';

const COLUMNS: Array<{ key: SortKey; label: string; align?: 'right' }> = [
  { key: 'symbol', label: 'Symbol' },
  { key: 'cost_basis', label: 'Cost Basis', align: 'right' },
  { key: 'proceeds', label: 'Proceeds', align: 'right' },
  { key: 'realized_pnl', label: 'Realized $', align: 'right' },
];

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

/** "3 sales", "1 sale". */
function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" sx={color ? { color } : undefined}>{value}</Typography>
    </Stack>
  );
}

function FlagChip({ label, hint }: { label: string; hint: string }) {
  return (
    <Tooltip title={hint}>
      <Chip
        size="small"
        label={label}
        sx={{ ml: 1, bgcolor: 'var(--qc-tint-amber)', color: 'var(--qc-ink-amber)' }}
      />
    </Tooltip>
  );
}

const NO_FX_HINT =
  'No exchange rate is recorded for this sale, so it has no dollar figure and is in no total.';
const NO_BASIS_HINT =
  'The lot has no purchase price, so this sale has no realized figure and is in no total.';

/** One sale's realized figure. A sale with no dollar figure says why, and
 * shows the figure in its own currency when it has one. */
function SaleRealized({ sale }: { sale: RealizedSale }) {
  if (sale.basis_missing) {
    return <FlagChip label="no purchase price" hint={NO_BASIS_HINT} />;
  }
  if (sale.fx_missing) {
    return (
      <>
        {formatCurrency(sale.realized_pnl_native, sale.currency)}
        <FlagChip label="no FX rate" hint={NO_FX_HINT} />
      </>
    );
  }
  return (
    <Box component="span" sx={{ color: gainColor(sale.realized_pnl) }}>
      {formatCurrency(sale.realized_pnl)}
    </Box>
  );
}

function SalesTable({ row }: { row: RealizedSymbolRow }) {
  return (
    <Box sx={{ pl: 6, pr: 2, py: 1 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {formatShares(row.shares_closed)} shares closed in {count(row.sale_count, 'sale')}
        {row.realized_pnl_pct != null && ` · realized ${formatPercentRaw(row.realized_pnl_pct)}`}
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Bought</TableCell>
            <TableCell>Sold</TableCell>
            <TableCell align="right">Shares</TableCell>
            <TableCell align="right">Price Paid</TableCell>
            <TableCell align="right">Sale Price</TableCell>
            <TableCell align="right">Realized $</TableCell>
            <TableCell align="right">Realized %</TableCell>
            <TableCell>Note</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {row.sales.map((sale) => (
            <TableRow key={sale.sale_id} data-testid={`realized-sale-${sale.sale_id}`}>
              <TableCell>{formatDate(sale.trade_date)}</TableCell>
              <TableCell>{formatDate(sale.sale_trade_date)}</TableCell>
              <TableCell align="right">{formatShares(sale.shares_sold)}</TableCell>
              {/* Prices are in the lot's own currency; the realized figure is dollars. */}
              <TableCell align="right">{formatCurrency(sale.purchase_price, sale.currency)}</TableCell>
              <TableCell align="right">{formatCurrency(sale.sale_price, sale.currency)}</TableCell>
              <TableCell align="right">
                <SaleRealized sale={sale} />
              </TableCell>
              <TableCell align="right" sx={{ color: gainColor(sale.realized_pnl_pct) }}>
                {sale.realized_pnl_pct == null ? '—' : formatPercentRaw(sale.realized_pnl_pct)}
              </TableCell>
              <TableCell sx={{ maxWidth: 280, whiteSpace: 'pre-wrap' }}>{sale.notes ?? ''}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

/** Which sales are left out of the totals, and why. Nothing when none are. */
function LeftOut({ totals }: { totals: RealizedResponse['totals'] }) {
  const parts: string[] = [];
  if (totals.fx_missing_count > 0) {
    parts.push(`${count(totals.fx_missing_count, 'sale')} with no FX rate`);
  }
  if (totals.basis_missing_count > 0) {
    parts.push(`${count(totals.basis_missing_count, 'sale')} with no purchase price`);
  }
  if (parts.length === 0) return null;
  return (
    <Alert severity="warning" sx={{ mb: 2 }} data-testid="realized-left-out">
      Left out of the totals: {parts.join(', and ')}. Expand a symbol to see which.
    </Alert>
  );
}

function sortRows(rows: RealizedSymbolRow[], key: SortKey, dir: 'asc' | 'desc'): RealizedSymbolRow[] {
  // The server's order is realized P/L, best first, with its own tie-breaks.
  if (key === 'realized_pnl' && dir === 'desc') return rows;
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    // A symbol with no figure stays last in both directions.
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string' || typeof bv === 'string') {
      return sign * String(av).localeCompare(String(bv));
    }
    return sign * (av - bv);
  });
}

function RealizedBody({ data, period }: { data: RealizedResponse; period: RealizedPeriod }) {
  const [sortKey, setSortKey] = useState<SortKey>('realized_pnl');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const sorted = useMemo(
    () => sortRows(data.symbols, sortKey, sortDir),
    [data.symbols, sortKey, sortDir],
  );

  if (data.symbols.length === 0) {
    const window = PERIODS.find((p) => p.value === period)?.empty;
    return (
      <Box sx={{ textAlign: 'center', py: 4 }} data-testid="realized-empty">
        <Typography variant="h6" gutterBottom>
          {window ? `No sales ${window}` : 'Nothing has been sold yet'}
        </Typography>
        <Typography color="text.secondary" sx={{ maxWidth: 480, mx: 'auto' }}>
          {window
            ? 'Choose a longer period to see earlier sales.'
            : 'When a lot is closed, what it made or lost is shown here.'}
        </Typography>
      </Box>
    );
  }

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      // Money columns open largest first; the symbol column opens A to Z.
      setSortDir(key === 'symbol' ? 'asc' : 'desc');
    }
  };

  const toggle = (symbol: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });

  const { totals } = data;

  return (
    <>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
          <Stat
            label="Realized P/L"
            value={formatCurrency(totals.realized_pnl)}
            color={gainColor(totals.realized_pnl)}
          />
          <Stat
            label="Realized %"
            value={formatPercentRaw(totals.realized_pnl_pct)}
            color={gainColor(totals.realized_pnl_pct)}
          />
          <Stat label="Cost Basis" value={formatCurrency(totals.cost_basis)} />
          <Stat label="Proceeds" value={formatCurrency(totals.proceeds)} />
          <Stat
            label="Sales"
            value={`${totals.sale_count} in ${count(totals.symbol_count, 'symbol')}`}
          />
        </Stack>
      </Paper>

      <LeftOut totals={totals} />

      {/* The chart draws nothing when no symbol has a dollar figure, so the
          Paper goes too. */}
      {data.symbols.some((row) => row.bar_base != null) && (
        <Paper sx={{ p: 2, mb: 2 }}>
          <PortfolioAllocationChart rows={data.symbols} variant="realized" />
        </Paper>
      )}

      <Paper sx={{ overflowX: 'auto' }}>
        <Table size="small" data-testid="realized-table">
          <TableHead>
            <TableRow>
              <TableCell />
              {COLUMNS.map((col) => (
                <TableCell key={col.key} align={col.align}>
                  <TableSortLabel
                    active={sortKey === col.key}
                    direction={sortKey === col.key ? sortDir : 'asc'}
                    onClick={() => handleSort(col.key)}
                  >
                    {col.label}
                  </TableSortLabel>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {sorted.map((row) => {
              const isOpen = expanded.has(row.symbol);
              return (
                <Fragment key={row.symbol}>
                  <TableRow hover data-testid={`realized-row-${row.symbol}`}>
                    <TableCell sx={{ width: 48 }}>
                      <IconButton
                        size="small"
                        aria-label={`${isOpen ? 'Hide' : 'Show'} ${row.symbol} sales`}
                        aria-expanded={isOpen}
                        onClick={() => toggle(row.symbol)}
                      >
                        {isOpen ? <KeyboardArrowDownIcon fontSize="small" /> : <KeyboardArrowRightIcon fontSize="small" />}
                      </IconButton>
                    </TableCell>
                    <TableCell>
                      <Typography component="span" variant="body2" sx={{ fontWeight: 600 }}>
                        {row.symbol}
                      </Typography>
                      {row.still_held && (
                        <Tooltip title="This book still holds an open lot of this symbol: a partial exit.">
                          <Chip
                            size="small"
                            label="still held"
                            sx={{ ml: 1, bgcolor: 'var(--qc-tint-blue)', color: 'var(--qc-ink-blue)' }}
                          />
                        </Tooltip>
                      )}
                    </TableCell>
                    <TableCell align="right">{formatCurrency(row.cost_basis)}</TableCell>
                    <TableCell align="right">{formatCurrency(row.proceeds)}</TableCell>
                    <TableCell align="right" sx={{ color: gainColor(row.realized_pnl) }}>
                      {formatCurrency(row.realized_pnl)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell
                      colSpan={COLUMNS.length + 1}
                      sx={{ p: 0, borderBottom: isOpen ? undefined : 'none' }}
                    >
                      <Collapse in={isOpen} unmountOnExit>
                        <SalesTable row={row} />
                      </Collapse>
                    </TableCell>
                  </TableRow>
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </Paper>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        In US dollars. Excludes fees and tax adjustments: no basis adjustment or wash-sale rule is
        applied.
      </Typography>
    </>
  );
}

export default function RealizedPnl({ bookId = null, title = 'Realized' }: Props) {
  // The period is this view's own state (decision 13): it is not shared with
  // any other section, and it is not a prop.
  const [period, setPeriod] = useState<RealizedPeriod>('year');
  const { data, isLoading, error } = useRealized(period, bookId);

  return (
    <Box data-testid="realized-pnl">
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
        spacing={2}
        sx={{ mb: 2 }}
      >
        <Stack direction="row" spacing={2} alignItems="baseline" flexWrap="wrap" useFlexGap>
          <Typography variant="h5" component="h2">{title}</Typography>
          {data?.start_date && data.end_date && (
            <Typography variant="caption" color="text.secondary">
              Sold {formatDate(data.start_date)} to {formatDate(data.end_date)}
            </Typography>
          )}
        </Stack>
        <TextField
          select
          size="small"
          label="Period"
          value={period}
          onChange={(e) => setPeriod(e.target.value as RealizedPeriod)}
          slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Realized period' } }}
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </TextField>
      </Stack>

      {isLoading ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
          <CircularProgress size={18} />
          <Typography variant="body2" color="text.secondary">Loading realized P/L…</Typography>
        </Box>
      ) : error || !data ? (
        <Alert severity="error">Couldn&apos;t load realized P/L.</Alert>
      ) : (
        <RealizedBody data={data} period={period} />
      )}
    </Box>
  );
}
