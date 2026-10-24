import { Alert, Box, Button, CircularProgress, Paper, Stack, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import { useRefreshOptionsSnapshots } from '../../hooks/useSecurities';

/** Manual trigger for today's options snapshot capture (P/C ratio and IV trend data). */
export default function OptionsRefreshPanel() {
  const { mutate, isPending, data, error, reset } = useRefreshOptionsSnapshots();

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap" useFlexGap>
        <Box sx={{ flex: 1, minWidth: 200 }}>
          <Typography variant="subtitle2">Options Snapshot Collection</Typography>
          <Typography variant="caption" sx={{ color: 'var(--qc-muted)' }}>
            Captures today's P/C ratio &amp; IV for all securities. Run once per trading day to build trend data.
            yfinance only provides the current chain — past snapshots cannot be backfilled.
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={isPending ? <CircularProgress size={14} /> : <RefreshIcon />}
          disabled={isPending}
          onClick={() => { reset(); mutate({ source: 'all', chainType: 'atm' }); }}
        >
          {isPending ? 'Collecting…' : 'Refresh All (ATM)'}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="secondary"
          startIcon={isPending ? <CircularProgress size={14} /> : <RefreshIcon />}
          disabled={isPending}
          onClick={() => { reset(); mutate({ source: 'portfolio', chainType: 'full' }); }}
        >
          {isPending ? 'Collecting…' : 'Refresh Portfolio (Full Chain)'}
        </Button>
        {data && !isPending && (
          <Stack direction="row" spacing={1} alignItems="center">
            <CheckCircleOutlineIcon sx={{ color: '#10b981', fontSize: 18 }} />
            <Typography variant="caption" sx={{ color: '#10b981' }}>
              {data.succeeded}/{data.total} succeeded in {data.duration_seconds}s
            </Typography>
            {data.failed > 0 && (
              <Typography variant="caption" sx={{ color: '#ef4444' }}>
                · {data.failed} failed
              </Typography>
            )}
          </Stack>
        )}
        {error && !isPending && (
          <Alert severity="error" onClose={reset} sx={{ fontSize: 12, py: 0 }}>
            {(error as Error).message || 'Refresh failed — check that the API server is running.'}
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}
