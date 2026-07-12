/**
 * Self-fetching wrapper so the chat registry can render the (props-driven)
 * PriceChart from just a ticker — mirrors SecurityDetailPage's composition.
 */
import { Alert, Box, CircularProgress, Typography } from '@mui/material';
import PriceChart from '../securities/charts/PriceChart';
import { useTechnicals } from '../../hooks/useSecurities';

export default function PriceChartCard({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useTechnicals(ticker, 365);

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Loading {ticker} price history…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load {ticker} price history.</Alert>;
  }
  const indicators = data?.indicators ?? [];
  if (indicators.length === 0) {
    return <Alert severity="info">No price history available for {ticker}.</Alert>;
  }
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        {ticker} — price &amp; moving averages
      </Typography>
      <PriceChart data={indicators} height={220} />
    </Box>
  );
}
