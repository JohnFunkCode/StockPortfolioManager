/**
 * BookSummaryStrip — the selected book at a glance, above the sections
 * (decision 7): what it is worth, what the open lots are up or down, and what
 * the closed ones made over the last year, with links down the page.
 *
 * Row-bound: it shows figures its parent already fetched and computes none.
 * A figure that is not known is left out, never shown as a zero or a dash
 * that reads as a value: its request is still loading or failed, or the part
 * of the plan that supplies it (return vs SPY, 5.3) is not built yet.
 *
 * Cash (3.1) is shown beside the holdings, not added to them: a sum made here
 * would be a figure the server never computed (Rule 8.4). While cash is
 * shown the first figure is called "Holdings value", because "Book value"
 * next to a separate cash figure would read as if it included it. One book
 * value that counts both comes with the daily snapshots (5.1).
 */
import { Link, Paper, Stack, Typography } from '@mui/material';
import { formatCurrency, formatPercentRaw } from '../../utils/formatting';
import type { PortfolioTotals, RealizedTotals } from '../../api/portfolioTypes';

interface Props {
  /** Open-lot totals for the book, when its holdings have loaded. */
  holdings?: PortfolioTotals | null;
  /** The book's realized totals for the last year, when they have loaded. */
  realizedYear?: RealizedTotals | null;
  /** The book's cash balance, when its ledger has loaded and has one. */
  cash?: number | null;
}

/** The sections of the Portfolio page, in page order. The ids are the jump
 * links' targets, so the page sets them on its section headings. */
export const PORTFOLIO_SECTIONS = [
  { id: 'holdings', label: 'Holdings' },
  { id: 'realized', label: 'Realized' },
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

export default function BookSummaryStrip({ holdings, realizedYear, cash }: Props) {
  const value = holdings?.total_current_value;
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
