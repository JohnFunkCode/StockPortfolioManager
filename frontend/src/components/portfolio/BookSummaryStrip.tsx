/**
 * BookSummaryStrip — the selected book at a glance, above the sections
 * (decision 7): what it is worth, what the open lots are up or down, and what
 * the closed ones made over the last year, with links down the page.
 *
 * Row-bound: it shows figures its parent already fetched and computes none.
 * A figure that is not known is left out, never shown as a zero or a dash
 * that reads as a value: its request is still loading or failed, or the book
 * has no snapshot yet to measure a return from.
 *
 * Cash (3.1) is shown beside the holdings, not added to them: a sum made here
 * would be a figure the server never computed (Rule 8.4). While cash is
 * shown the first figure is called "Holdings value", because "Book value"
 * next to a separate cash figure would read as if it included it. One book
 * value that counts both is the newest daily snapshot's (5.1), shown with the
 * close it is from, and the return against SPY is the server's time-weighted
 * figure over the last year (5.3). A return the server marks unreliable
 * (cash below zero, decision 8) says so.
 */
import { Link, Paper, Stack, Typography } from '@mui/material';
import { formatCurrency, formatDate, formatPercent, formatPercentRaw } from '../../utils/formatting';
import type { PortfolioTotals, RealizedTotals } from '../../api/portfolioTypes';
import type { BookPerformance } from '../../api/books';

interface Props {
  /** Open-lot totals for the book, when its holdings have loaded. */
  holdings?: PortfolioTotals | null;
  /** The book's realized totals for the last year, when they have loaded. */
  realizedYear?: RealizedTotals | null;
  /** The book's cash balance, when its ledger has loaded and has one. */
  cash?: number | null;
  /** The book's performance over the last year, when it has loaded. */
  performanceYear?: BookPerformance | null;
}

/** The sections of the Portfolio page, in page order. The ids are the jump
 * links' targets, so the page sets them on its section headings. */
export const PORTFOLIO_SECTIONS = [
  { id: 'holdings', label: 'Holdings' },
  { id: 'realized', label: 'Realized' },
  { id: 'performance', label: 'Performance' },
  { id: 'cash', label: 'Cash and orders' },
] as const;

function gainColor(value: number): string {
  return value >= 0 ? '#10b981' : '#ef4444';
}

function Stat({
  label,
  value,
  detail,
  color,
}: {
  label: string;
  value: string;
  detail?: string;
  color?: string;
}) {
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" sx={color ? { color } : undefined}>
        {value}
        {detail && (
          <Typography component="span" variant="body2" sx={{ ml: 1 }}>
            {detail}
          </Typography>
        )}
      </Typography>
    </Stack>
  );
}

/** "+3.10% vs SPY". */
function versus(performance: BookPerformance): string | undefined {
  if (performance.vs_benchmark == null) return undefined;
  const sign = performance.vs_benchmark >= 0 ? '+' : '';
  return `${sign}${formatPercent(performance.vs_benchmark)} vs ${performance.benchmark_symbol}`;
}

export default function BookSummaryStrip({ holdings, realizedYear, cash, performanceYear }: Props) {
  const value = holdings?.total_current_value;
  const latest = performanceYear?.latest;
  const twr = performanceYear?.twr;
  const unreliable = (performanceYear?.cash_negative_count ?? 0) > 0;
  const unrealized = holdings?.total_gain_loss;
  const unrealizedPct = holdings?.total_gain_loss_pct;
  const realized = realizedYear?.realized_pnl;

  return (
    <Paper sx={{ p: 2, mb: 2 }} data-testid="book-summary-strip">
      <Stack
        direction="row"
        spacing={4}
        flexWrap="wrap"
        useFlexGap
        justifyContent="space-between"
        alignItems="flex-end"
      >
        <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
          {value != null && (
            <Stat label={cash != null ? 'Holdings value' : 'Book value'} value={formatCurrency(value)} />
          )}
          {cash != null && (
            <Stat label="Cash" value={formatCurrency(cash)} color={cash < 0 ? '#ef4444' : undefined} />
          )}
          {unrealized != null && (
            <Stat
              label="Unrealized"
              value={formatCurrency(unrealized)}
              detail={unrealizedPct != null ? formatPercentRaw(unrealizedPct) : undefined}
              color={gainColor(unrealized)}
            />
          )}
          {latest?.book_value != null && (
            <Stat
              label={`Book value, ${formatDate(latest.session_date)} close`}
              value={formatCurrency(latest.book_value)}
            />
          )}
          {performanceYear && twr != null && (
            <Stat
              label={unreliable ? 'Return, last year (unreliable)' : 'Return, last year'}
              value={formatPercent(twr)}
              detail={versus(performanceYear)}
              color={unreliable ? undefined : gainColor(performanceYear.vs_benchmark ?? twr)}
            />
          )}
          {realized != null && (
            <Stat
              label="Realized, last year"
              value={formatCurrency(realized)}
              color={gainColor(realized)}
            />
          )}
        </Stack>
        <Stack component="nav" aria-label="Portfolio sections" direction="row" spacing={2}>
          {PORTFOLIO_SECTIONS.map((section) => (
            <Link key={section.id} href={`#${section.id}`} underline="hover" variant="body2">
              {section.label}
            </Link>
          ))}
        </Stack>
      </Stack>
    </Paper>
  );
}
