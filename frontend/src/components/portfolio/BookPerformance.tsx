/**
 * BookPerformance — one book's time-weighted return and drawdown against SPY,
 * from its daily snapshots, with realized P/L by what prompted each sale
 * (#431 step 5.3).
 *
 * One request, `GET /api/books/performance`, feeds the figures, the chart, the
 * notes and the attribution table. The period pulldown changes the request,
 * and is this section's own: it is not shared with the Realized section
 * (decision 13). No figure is computed here (Rule 8.4).
 *
 * Self-contained: `bookId` names a book and null is the real book. The
 * Portfolio page mounts it as its Performance section, and the Sidekick's
 * `book_performance` card mounts it with no props at all.
 */
import { useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  Alert,
  Box,
  CircularProgress,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import { useBookPerformance } from '../../hooks/useBooks';
import PerformanceChart from './PerformanceChart';
import { formatCurrency, formatDate, formatDateTime, formatPercent } from '../../utils/formatting';
import { SALE_SOURCES } from './stopWords';
import type { BookPerformance as Performance, PerformancePeriod } from '../../api/books';

interface Props {
  /** One of the caller's books; null or absent is the real book. */
  bookId?: number | null;
  title?: string;
  /** The link to the Compare books view. The Sidekick card leaves it out. */
  showCompareLink?: boolean;
}

export const PERFORMANCE_PERIODS: Array<{ value: PerformancePeriod; label: string }> = [
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
  { value: 'all', label: 'All' },
];

/** What every performance figure leaves out, said wherever one is shown. */
export const PERFORMANCE_CAVEATS =
  'Price returns only: dividends are not counted, for a book or for SPY. Stock splits are not ' +
  'adjusted yet (#391), so a split shows as a fall.';

export function gainColor(value: number | null | undefined): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

/** "3 sessions", "1 session". */
export function sessions(n: number): string {
  return `${n} session${n === 1 ? '' : 's'}`;
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" sx={color ? { color } : undefined}>{value}</Typography>
    </Stack>
  );
}

/** Everything that qualifies the figures above it, one line each. A note
 * appears only when it applies. */
export function PerformanceNotes({ data }: { data: Performance }) {
  const latest = data.latest;
  const incomplete = latest ? latest.unpriced_count + latest.fx_missing_count : 0;
  return (
    <Stack spacing={1} sx={{ mb: 2 }}>
      {data.cash_negative_count > 0 && (
        <Alert severity="warning" data-testid="cash-negative-note">
          Cash was below zero on {sessions(data.cash_negative_count)} in this window, so the return
          is unreliable: a deposit is probably missing from the cash ledger.
        </Alert>
      )}
      {data.restated_count > 0 && (
        <Alert severity="info" data-testid="restated-note">
          {sessions(data.restated_count)} restated after a late change to the book&apos;s history
          {data.last_restated_at ? `, most recently ${formatDateTime(data.last_restated_at)}` : ''}.
        </Alert>
      )}
      {data.gaps.length > 0 && (
        <Alert severity="info" data-testid="gaps-note">
          {sessions(data.gaps.length)} could not be valued or measured, and the return is chained
          across {data.gaps.length === 1 ? 'it' : 'them'}.
        </Alert>
      )}
      {latest && incomplete > 0 && (
        <Alert severity="warning" data-testid="incomplete-note">
          The book has no value at the {formatDate(latest.session_date)} close: a holding had no
          close in the week before it, or no exchange rate.
        </Alert>
      )}
      {latest && incomplete === 0 && latest.stale_count > 0 && (
        <Alert severity="info" data-testid="stale-note">
          At the {formatDate(latest.session_date)} close, {latest.stale_count} holding
          {latest.stale_count === 1 ? ' was' : 's were'} valued at an earlier close.
        </Alert>
      )}
      {!data.benchmark_available && (
        <Alert severity="info" data-testid="benchmark-note">
          SPY&apos;s prices could not be read, so there is no comparison this time.
        </Alert>
      )}
    </Stack>
  );
}

/** Realized P/L by what prompted each sale, over the same window. */
function Attribution({ data }: { data: Performance }) {
  const { sources, totals } = data.attribution;
  if (sources.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No sales in this window.
      </Typography>
    );
  }
  return (
    <Paper sx={{ overflowX: 'auto' }}>
      <Table size="small" data-testid="attribution-table">
        <TableHead>
          <TableRow>
            <TableCell>Source</TableCell>
            <TableCell align="right">Sales</TableCell>
            <TableCell align="right">Realized $</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {sources.map((row) => (
            <TableRow key={row.source ?? 'manual'} data-testid={`attribution-${row.source}`}>
              <TableCell>{SALE_SOURCES[row.source ?? 'manual'] ?? row.source}</TableCell>
              <TableCell align="right">{row.sale_count}</TableCell>
              <TableCell align="right" sx={{ color: gainColor(row.realized_pnl) }}>
                {formatCurrency(row.realized_pnl)}
              </TableCell>
            </TableRow>
          ))}
          <TableRow>
            <TableCell sx={{ fontWeight: 600 }}>Total</TableCell>
            <TableCell align="right">{totals.sale_count}</TableCell>
            <TableCell align="right" sx={{ fontWeight: 600, color: gainColor(totals.realized_pnl) }}>
              {formatCurrency(totals.realized_pnl)}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </Paper>
  );
}

function PerformanceBody({ data }: { data: Performance }) {
  if (data.series.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" data-testid="performance-empty">
        {data.kind === 'real'
          ? "No history yet. The real book's history starts at its opening balance, and the daily job values it after each close."
          : 'No history yet. The daily job values the book after each close.'}
      </Typography>
    );
  }
  const unreliable = data.cash_negative_count > 0;
  return (
    <>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
          <Stat
            label={unreliable ? 'Return (unreliable)' : 'Return'}
            value={formatPercent(data.twr)}
            color={unreliable ? undefined : gainColor(data.twr)}
          />
          <Stat label={data.benchmark_symbol} value={formatPercent(data.benchmark_return)} />
          <Stat
            label={`vs ${data.benchmark_symbol}`}
            value={formatPercent(data.vs_benchmark)}
            color={unreliable ? undefined : gainColor(data.vs_benchmark)}
          />
          <Stat label="Max drawdown" value={formatPercent(data.max_drawdown)} />
          {data.latest?.book_value != null && (
            <Stat
              label={`Book value, ${formatDate(data.latest.session_date)} close`}
              value={formatCurrency(data.latest.book_value)}
            />
          )}
        </Stack>
      </Paper>

      <PerformanceNotes data={data} />

      <Paper sx={{ p: 2, mb: 2 }}>
        <PerformanceChart
          lines={[
            {
              key: 'book',
              label: data.name,
              points: data.series.map((p) => ({ date: p.session_date, value: p.index })),
            },
            {
              key: 'benchmark',
              label: data.benchmark_symbol,
              benchmark: true,
              points: data.series.map((p) => ({ date: p.session_date, value: p.benchmark_index })),
            },
          ]}
        />
      </Paper>

      <Typography variant="subtitle1" component="h3" sx={{ mb: 1 }}>
        Realized by source
      </Typography>
      <Attribution data={data} />

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Time-weighted from the daily snapshots, so a deposit or withdrawal is not a gain or a loss.{' '}
        {PERFORMANCE_CAVEATS}
      </Typography>
    </>
  );
}

export default function BookPerformance({
  bookId = null,
  title = 'Performance',
  showCompareLink = true,
}: Props) {
  const [period, setPeriod] = useState<PerformancePeriod>('year');
  const { data, isLoading, error } = useBookPerformance(period, bookId);

  return (
    <Box data-testid="book-performance">
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
          {data?.first_session && data.last_session && (
            <Typography variant="caption" color="text.secondary">
              {formatDate(data.first_session)} to {formatDate(data.last_session)}
            </Typography>
          )}
          {showCompareLink && (
            <Link component={RouterLink} to="/books/compare" variant="body2" underline="hover">
              Compare books
            </Link>
          )}
        </Stack>
        <TextField
          select
          size="small"
          label="Period"
          value={period}
          onChange={(e) => setPeriod(e.target.value as PerformancePeriod)}
          slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Performance period' } }}
        >
          {PERFORMANCE_PERIODS.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </TextField>
      </Stack>

      {isLoading ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
          <CircularProgress size={18} />
          <Typography variant="body2" color="text.secondary">Loading performance…</Typography>
        </Box>
      ) : error || !data ? (
        <Alert severity="error">Couldn&apos;t load performance.</Alert>
      ) : (
        <PerformanceBody data={data} />
      )}
    </Box>
  );
}
