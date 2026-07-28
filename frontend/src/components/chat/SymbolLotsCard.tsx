/**
 * Self-fetching chat card for the Sidekick — renders the caller's own lots
 * for one symbol. Read-only, same rationale as PortfolioTableCard: no
 * edit/delete/close actions (decision #20).
 *
 * Filters the shared portfolio-symbols response client-side rather than
 * hitting a dedicated endpoint, since none exists for a single symbol.
 */
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { useSymbolRows } from '../../hooks/usePortfolio';
import {
  formatCurrency,
  formatDollarsPerDay,
  formatPercentRaw,
  formatShares,
} from '../../utils/formatting';

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

export default function SymbolLotsCard({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useSymbolRows();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Loading {ticker} lots…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load lots for {ticker}.</Alert>;
  }

  const row = (data?.symbols ?? []).find((s) => s.symbol === ticker);
  if (!row) {
    return <Alert severity="info">No {ticker} lots in your portfolio.</Alert>;
  }

  return (
    <Box data-testid="symbol-lots-card">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        {ticker} — Lots
      </Typography>
      <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
        <Metric label="Investment" value={formatCurrency(row.total_investment)} />
        <Metric label="Current Value" value={formatCurrency(row.total_current_value)} />
        <Metric
          label="Gain/Loss"
          value={formatCurrency(row.total_gain_loss)}
          color={gainColor(row.total_gain_loss)}
        />
        <Metric
          label="Gain/Loss %"
          value={formatPercentRaw(row.total_gain_loss_pct)}
          color={gainColor(row.total_gain_loss_pct)}
        />
        <Metric label="$/Day" value={formatDollarsPerDay(row.total_dollars_per_day)} />
      </Stack>
      <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Price Paid</TableCell>
              <TableCell>Shares</TableCell>
              <TableCell>Gain/Loss %</TableCell>
              <TableCell>Gain/Loss $</TableCell>
              <TableCell>Days Held</TableCell>
              <TableCell>$/Day</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {row.lots.map((lot) => (
              <TableRow key={lot.lot_id}>
                <TableCell>{formatCurrency(lot.purchase_price)}</TableCell>
                <TableCell>{formatShares(lot.quantity)}</TableCell>
                <TableCell sx={{ color: gainColor(lot.gain_loss_pct) }}>
                  {formatPercentRaw(lot.gain_loss_pct)}
                </TableCell>
                <TableCell sx={{ color: gainColor(lot.gain_loss) }}>
                  {formatCurrency(lot.gain_loss)}
                </TableCell>
                <TableCell>{lot.days_held ?? '—'}</TableCell>
                <TableCell>{formatDollarsPerDay(lot.dollars_per_day)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography variant="body2" sx={color ? { color, fontWeight: 600 } : { fontWeight: 600 }}>
        {value}
      </Typography>
    </Box>
  );
}
