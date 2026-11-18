/**
 * CompareBooksPage — every open book against SPY, side by side (#431 step
 * 5.3, decision 15).
 *
 * Its own view, opened from the Performance section, so the Portfolio page
 * stays about one book and this heavier read happens only when asked for. One
 * request, `GET /api/books/performance/compare`, gives each book's series and
 * figures over the same period; the range control is this view's own. There
 * is no combined total across books, real and paper (a rule from #431), and
 * no figure is computed here (Rule 8.4).
 */
import { useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  Alert,
  Box,
  Chip,
  Link,
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
import { useCompareBooks } from '../../hooks/useBooks';
import LoadingSpinner from '../common/LoadingSpinner';
import PerformanceChart from './PerformanceChart';
import type { ChartLine } from './PerformanceChart';
import { PERFORMANCE_CAVEATS, PERFORMANCE_PERIODS, gainColor, sessions } from './BookPerformance';
import { formatCurrency, formatDate, formatPercent } from '../../utils/formatting';
import type { BookPerformance, CompareBooks, PerformancePeriod } from '../../api/books';

/** The Portfolio page with a book selected. */
function bookHref(book: BookPerformance): string {
  return book.kind === 'real' || book.book_id === null ? '/' : `/?book=${book.book_id}`;
}

/** The chart: one line a book that has a series, and SPY once. SPY's line
 * comes from the book with the longest series, whose first session is the
 * earliest base: every book's SPY index starts at its own first session. */
function chartLines(data: CompareBooks): ChartLine[] {
  const books = data.books.filter((b) => b.series.length > 0);
  const lines: ChartLine[] = books.map((b) => ({
    key: `book-${b.book_id ?? 'real'}`,
    label: b.name,
    points: b.series.map((p) => ({ date: p.session_date, value: p.index })),
  }));
  const longest = books.reduce<BookPerformance | null>(
    (best, b) => (best === null || b.series.length > best.series.length ? b : best),
    null,
  );
  if (longest && longest.benchmark_available) {
    lines.push({
      key: 'benchmark',
      label: longest.benchmark_symbol,
      benchmark: true,
      points: longest.series.map((p) => ({ date: p.session_date, value: p.benchmark_index })),
    });
  }
  return lines;
}

function BookRow({ book }: { book: BookPerformance }) {
  const unreliable = book.cash_negative_count > 0;
  return (
    <TableRow data-testid={`compare-row-${book.book_id ?? 'real'}`}>
      <TableCell>
        <Link component={RouterLink} to={bookHref(book)} underline="hover" sx={{ fontWeight: 600 }}>
          {book.name}
        </Link>
        {book.kind === 'paper' && (
          <Chip size="small" label="paper" sx={{ ml: 1, bgcolor: 'var(--qc-tint-amber)', color: 'var(--qc-ink-amber)' }} />
        )}
        {unreliable && (
          <Tooltip
            title={`Cash was below zero on ${sessions(book.cash_negative_count)} in this window, so its return is unreliable.`}
          >
            <Chip size="small" label="unreliable" sx={{ ml: 1, bgcolor: 'var(--qc-tint-red)', color: 'var(--qc-ink-red)' }} />
          </Tooltip>
        )}
      </TableCell>
      <TableCell align="right">
        {book.latest?.book_value != null ? formatCurrency(book.latest.book_value) : '—'}
      </TableCell>
      <TableCell align="right" sx={{ color: unreliable ? undefined : gainColor(book.twr) }}>
        {formatPercent(book.twr)}
      </TableCell>
      <TableCell align="right" sx={{ color: unreliable ? undefined : gainColor(book.vs_benchmark) }}>
        {formatPercent(book.vs_benchmark)}
      </TableCell>
      <TableCell align="right">{formatPercent(book.max_drawdown)}</TableCell>
      <TableCell align="right" sx={{ color: gainColor(book.attribution.totals.realized_pnl) }}>
        {formatCurrency(book.attribution.totals.realized_pnl)}
      </TableCell>
    </TableRow>
  );
}

function CompareBody({ data }: { data: CompareBooks }) {
  const lines = chartLines(data);
  const noBenchmark = data.books.some((b) => b.series.length > 0 && !b.benchmark_available);
  return (
    <>
      <Paper sx={{ p: 2, mb: 2 }}>
        <PerformanceChart lines={lines} height={360} />
      </Paper>
      {noBenchmark && (
        <Alert severity="info" sx={{ mb: 2 }}>
          SPY&apos;s prices could not be read, so there is no comparison this time.
        </Alert>
      )}
      <Paper sx={{ overflowX: 'auto' }}>
        <Table size="small" data-testid="compare-table">
          <TableHead>
            <TableRow>
              <TableCell>Book</TableCell>
              <TableCell align="right">Value</TableCell>
              <TableCell align="right">Return</TableCell>
              <TableCell align="right">vs SPY</TableCell>
              <TableCell align="right">Max drawdown</TableCell>
              <TableCell align="right">Realized</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.books.map((book) => (
              <BookRow key={book.book_id ?? 'real'} book={book} />
            ))}
          </TableBody>
        </Table>
      </Paper>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Each book is measured from its own first snapshot in the period, and so is its comparison
        with SPY. Value is at the newest close. Books are never added together.{' '}
        {PERFORMANCE_CAVEATS}
      </Typography>
    </>
  );
}

export default function CompareBooksPage() {
  const [period, setPeriod] = useState<PerformancePeriod>('year');
  const { data, isLoading, error } = useCompareBooks(period);

  return (
    <Box data-testid="compare-books">
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
          <Typography variant="h4">Compare books</Typography>
          {data?.start_date && data.end_date && (
            <Typography variant="caption" color="text.secondary">
              {formatDate(data.start_date)} to {formatDate(data.end_date)}
            </Typography>
          )}
          <Link component={RouterLink} to="/#performance" variant="body2" underline="hover">
            Back to the portfolio
          </Link>
        </Stack>
        <TextField
          select
          size="small"
          label="Range"
          value={period}
          onChange={(e) => setPeriod(e.target.value as PerformancePeriod)}
          slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Compare range' } }}
        >
          {PERFORMANCE_PERIODS.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </TextField>
      </Stack>

      {isLoading ? (
        <LoadingSpinner />
      ) : error || !data ? (
        <Alert severity="error">Couldn&apos;t load the books&apos; performance.</Alert>
      ) : (
        <CompareBody data={data} />
      )}
    </Box>
  );
}
