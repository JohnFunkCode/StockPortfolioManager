/**
 * Self-fetching chat card for the Sidekick — the caller's own portfolio as the
 * stacked allocation chart (issue #147 Part A). Read-only, and no props ever:
 * the holdings come from the browser's authenticated session, never from an
 * `owner` the model could name (decision #20).
 *
 * Shares the ['portfolio-symbols'] query with the Portfolio page, so rendering
 * this alongside portfolio_table costs one fetch, not two. All the chart's
 * numbers arrive precomputed on the response (Rule 8.4).
 */
import { Alert, Box, CircularProgress, Typography } from '@mui/material';
import { useSymbolRows } from '../../hooks/usePortfolio';
import PortfolioAllocationChart from '../portfolio/PortfolioAllocationChart';

export default function PortfolioAllocationCard() {
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
  // Every symbol unpriced (a cold quote cache, say): the chart has nothing to
  // draw and returns null, so say why rather than rendering an empty box.
  if (rows.every((row) => row.bar_base == null)) {
    return <Alert severity="info">No priced positions to chart yet.</Alert>;
  }

  return (
    <Box data-testid="portfolio-allocation-card">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        Your allocation
      </Typography>
      <PortfolioAllocationChart rows={rows} />
    </Box>
  );
}
