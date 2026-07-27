/**
 * Self-fetching chat wrappers for the arbitrage visuals.
 *
 * The registry needs components that render from scalar props alone, while the
 * charts themselves take fetched data — these adapt one to the other, exactly
 * as PriceChartCard does for PriceChart. Keeping all four together makes the
 * shared loading/error shape obvious and hard to diverge.
 */
import { Alert, Box, CircularProgress, Typography } from '@mui/material';
import SpreadChart from '../arbitrage/SpreadChart';
import PremiumChart from '../arbitrage/PremiumChart';
import ScanTable from '../arbitrage/ScanTable';
import DiscoveryScatter from '../arbitrage/DiscoveryScatter';
import {
  useSpreadHistory,
  usePremiumHistory,
  useArbitrageScan,
  useDiscovery,
} from '../../hooks/useArbitrage';

function Loading({ label }: { label: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
      <CircularProgress size={18} />
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
    </Box>
  );
}

export function SpreadHistoryCard({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useSpreadHistory(ticker);
  if (isLoading) return <Loading label={`Loading ${ticker} spread history…`} />;
  if (error) return <Alert severity="error">Couldn&apos;t load {ticker} spread history.</Alert>;
  if (!data) return null;
  if (data.error) return <Alert severity="info">{data.error}</Alert>;
  return <SpreadChart data={data} />;
}

export function PremiumHistoryCard({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = usePremiumHistory(ticker);
  if (isLoading) return <Loading label={`Loading ${ticker} NAV premium history…`} />;
  if (error) return <Alert severity="error">Couldn&apos;t load {ticker} premium history.</Alert>;
  if (!data) return null;
  // Non-NAV securities land here routinely — the service explains why, and
  // that explanation is more useful than a generic failure.
  if (data.error) return <Alert severity="info">{data.error}</Alert>;
  return <PremiumChart data={data} />;
}

export function ArbScanCard() {
  const { data, isLoading, error } = useArbitrageScan();
  if (isLoading) return <Loading label="Scanning the curated universe…" />;
  if (error) return <Alert severity="error">Couldn&apos;t run the arbitrage scan.</Alert>;
  if (!data) return null;
  return <ScanTable rows={data.candidates} />;
}

export function DiscoveryCard({ symbols }: { symbols: string }) {
  const { data, isLoading, error } = useDiscovery(symbols, true);
  if (isLoading) return <Loading label={`Sweeping ${symbols} against the panel…`} />;
  if (error) return <Alert severity="error">Couldn&apos;t run the discovery sweep.</Alert>;
  if (!data) return null;
  return (
    <DiscoveryScatter tested={data.tested ?? []} criticalValues={data.critical_values} />
  );
}
