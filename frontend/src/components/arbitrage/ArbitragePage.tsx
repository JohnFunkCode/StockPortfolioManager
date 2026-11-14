/**
 * ArbitragePage — the scanner's home in QuantUI.
 *
 * Two tabs. Scan is the ranked table; Universe is every curated link with its
 * rationale, loaded on its own so it answers "what is watched, and why" while
 * a scan is still running or has failed (#415). Selecting a pair on either tab
 * opens the full workup beneath it (card + spread chart, plus the NAV premium
 * chart when the pair has one). A collapsible discovery panel runs
 * cointegration sweeps.
 *
 * Composed entirely from the same components the sidekick renders, so the two
 * surfaces cannot drift.
 */
import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  Paper,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import ScienceIcon from '@mui/icons-material/Science';
import ScanTable from './ScanTable';
import UniverseTable from './UniverseTable';
import SpreadChart from './SpreadChart';
import PremiumChart from './PremiumChart';
import DiscoveryScatter from './DiscoveryScatter';
import ArbitrageCard from '../chat/ArbitrageCard';
import {
  useArbitrageScan,
  useArbitrageUniverse,
  useSpreadHistory,
  usePremiumHistory,
  useDiscovery,
} from '../../hooks/useArbitrage';

function PairDetail({ security, kind }: { security: string; kind: string }) {
  const spread = useSpreadHistory(security);
  const premium = usePremiumHistory(security);
  const isNav = kind === 'nav_vehicle';

  return (
    <Stack spacing={2}>
      <ArbitrageCard ticker={security} />
      <Divider />
      {spread.isLoading && <CircularProgress size={18} />}
      {spread.data && !spread.data.error && <SpreadChart data={spread.data} />}
      {spread.data?.error && <Alert severity="info">{spread.data.error}</Alert>}
      {isNav && (
        <>
          {premium.isLoading && <CircularProgress size={18} />}
          {premium.data && !premium.data.error && <PremiumChart data={premium.data} />}
          {premium.data?.error && <Alert severity="info">{premium.data.error}</Alert>}
        </>
      )}
    </Stack>
  );
}

export default function ArbitragePage() {
  const { data, isLoading, error } = useArbitrageScan();
  const universe = useArbitrageUniverse();
  const [tab, setTab] = useState<'scan' | 'universe'>('scan');
  // A pair opened from either tab. The kind travels with it because a pair
  // whose scan errored has no scan row to read it from.
  const [selected, setSelected] = useState<{ security: string; kind: string } | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [symbolsInput, setSymbolsInput] = useState('');
  const [sweepFor, setSweepFor] = useState('');
  const discovery = useDiscovery(sweepFor, !!sweepFor);

  const rows = data?.candidates ?? [];
  const entries = universe.data?.entries ?? [];

  const openFromScan = (security: string) => {
    const row = rows.find((r) => r.security === security);
    if (row) setSelected({ security, kind: row.kind });
  };
  const openFromUniverse = (security: string) => {
    const entry = entries.find((e) => e.security === security);
    if (entry) setSelected({ security, kind: entry.kind });
  };

  // The workup opens below the table, which on the Universe tab can be off
  // screen under an expanded row.
  useEffect(() => {
    if (selected) detailRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
  }, [selected]);

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h4">Arbitrage</Typography>
        <Button
          size="small"
          startIcon={<ScienceIcon />}
          variant={discoveryOpen ? 'contained' : 'outlined'}
          onClick={() => setDiscoveryOpen((o) => !o)}
        >
          Discovery
        </Button>
      </Stack>

      <Collapse in={discoveryOpen}>
        <Paper sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Cointegration sweep against the commodity/crypto/FX panel
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
            <TextField
              size="small"
              label="Symbols"
              placeholder="NEM, AEM, FCX"
              value={symbolsInput}
              onChange={(e) => setSymbolsInput(e.target.value)}
              sx={{ minWidth: 260 }}
            />
            <Button
              variant="contained"
              size="small"
              disabled={!symbolsInput.trim()}
              onClick={() => setSweepFor(symbolsInput.trim())}
            >
              Sweep
            </Button>
          </Stack>
          {discovery.isLoading && (
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={16} />
              <Typography variant="body2" color="text.secondary">
                Testing each symbol against six references…
              </Typography>
            </Stack>
          )}
          {discovery.error && <Alert severity="error">Sweep failed.</Alert>}
          {discovery.data && (
            <DiscoveryScatter
              tested={discovery.data.tested ?? []}
              criticalValues={discovery.data.critical_values}
            />
          )}
        </Paper>
      </Collapse>

      <Tabs
        value={tab}
        onChange={(_, value) => setTab(value)}
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab value="scan" label="Scan" />
        <Tab
          value="universe"
          label={universe.data ? `Universe · ${universe.data.count}` : 'Universe'}
        />
      </Tabs>

      {tab === 'scan' && (
        <>
          {isLoading && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={20} />
              <Typography variant="body2" color="text.secondary">
                Scanning the curated universe — each pair is a full workup, so this
                takes a moment.
              </Typography>
            </Stack>
          )}
          {error && <Alert severity="error">{(error as Error).message}</Alert>}

          {data && (
            <>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="body2" color="text.secondary">
                  {data.returned} of {data.scanned} pairs
                </Typography>
                {data.errors.length > 0 && (
                  <Chip size="small" color="warning" variant="outlined"
                    label={`${data.errors.length} errored`} />
                )}
              </Stack>
              {data.errors.length > 0 && (
                <Alert severity="warning" sx={{ mb: 1 }} data-testid="scan-errors">
                  {data.errors.map((e) => (
                    <Typography key={e.security} variant="body2">
                      <strong>{e.security}</strong>: {e.error}
                    </Typography>
                  ))}
                </Alert>
              )}
              <ScanTable rows={rows} onSelect={openFromScan} />
            </>
          )}
        </>
      )}

      {tab === 'universe' && (
        <>
          {universe.isLoading && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={20} />
              <Typography variant="body2" color="text.secondary">
                Loading the curated universe…
              </Typography>
            </Stack>
          )}
          {universe.error && (
            <Alert severity="error">{(universe.error as Error).message}</Alert>
          )}
          {universe.data && (
            <UniverseTable entries={entries} onOpenWorkup={openFromUniverse} />
          )}
        </>
      )}

      {selected && (
        <Paper sx={{ p: 2, mt: 2 }} ref={detailRef}>
          <PairDetail security={selected.security} kind={selected.kind} />
        </Paper>
      )}
    </Box>
  );
}
