import { Paper, Stack, Typography } from '@mui/material';
import { formatCurrency, formatDollarsPerDay, formatPercentRaw } from '../../utils/formatting';
import type { PortfolioTotals } from '../../api/portfolioTypes';

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" sx={color ? { color } : undefined}>{value}</Typography>
    </Stack>
  );
}

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

export default function PortfolioSummary({ totals }: { totals: PortfolioTotals }) {
  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
        <Stat label="Total Investment" value={formatCurrency(totals.total_investment)} />
        <Stat label="Total Current Value" value={formatCurrency(totals.total_current_value)} />
        <Stat
          label="Total Gain/Loss"
          value={formatCurrency(totals.total_gain_loss)}
          color={gainColor(totals.total_gain_loss)}
        />
        <Stat
          label="Total Gain/Loss %"
          value={formatPercentRaw(totals.total_gain_loss_pct)}
          color={gainColor(totals.total_gain_loss_pct)}
        />
        <Stat label="Dollars Per Day" value={formatDollarsPerDay(totals.total_dollars_per_day)} />
      </Stack>
    </Paper>
  );
}
