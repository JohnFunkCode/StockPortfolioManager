/**
 * Self-fetching chat card for the Sidekick — renders the caller's own
 * portfolio (per-symbol roll-up + totals). Read-only: no edit/delete/close
 * actions, mirroring the Portfolio page's data but never its mutations
 * (decision #20 — Sidekick is display-only).
 *
 * Every number here comes straight from GET /api/portfolio/symbols. No
 * gain/loss, percentage, or totals math happens in this file (Rule 8.4).
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
import { formatCurrency, formatDollarsPerDay, formatPercentRaw } from '../../utils/formatting';

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

export default function PortfolioTableCard() {
  const { data, isLoading, error } = useSymbolRows();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Loading your portfolio…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load your portfolio.</Alert>;
  }
  const rows = data?.symbols ?? [];
  if (rows.length === 0) {
    return <Alert severity="info">No lots yet — the portfolio is empty.</Alert>;
  }

  const totals = data?.totals;

  return (
    <Box data-testid="portfolio-table-card">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        Your portfolio
      </Typography>
      {totals && (
        <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Metric label="Investment" value={formatCurrency(totals.total_investment)} />
          <Metric label="Current Value" value={formatCurrency(totals.total_current_value)} />
          <Metric
            label="Gain/Loss"
            value={formatCurrency(totals.total_gain_loss)}
            color={gainColor(totals.total_gain_loss)}
          />
          <Metric
            label="Gain/Loss %"
            value={formatPercentRaw(totals.total_gain_loss_pct)}
            color={gainColor(totals.total_gain_loss_pct)}
          />
          <Metric label="$/Day" value={formatDollarsPerDay(totals.total_dollars_per_day)} />
        </Stack>
      )}
      <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Symbol</TableCell>
              <TableCell align="right">Value</TableCell>
              <TableCell align="right">Gain/Loss $</TableCell>
              <TableCell align="right">Gain/Loss %</TableCell>
              <TableCell align="right">$/Day</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.symbol}>
                <TableCell>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {row.symbol}
                  </Typography>
                </TableCell>
                <TableCell align="right">{formatCurrency(row.total_current_value)}</TableCell>
                <TableCell align="right" sx={{ color: gainColor(row.total_gain_loss) }}>
                  {formatCurrency(row.total_gain_loss)}
                </TableCell>
                <TableCell align="right" sx={{ color: gainColor(row.total_gain_loss_pct) }}>
                  {formatPercentRaw(row.total_gain_loss_pct)}
                </TableCell>
                <TableCell align="right">{formatDollarsPerDay(row.total_dollars_per_day)}</TableCell>
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
