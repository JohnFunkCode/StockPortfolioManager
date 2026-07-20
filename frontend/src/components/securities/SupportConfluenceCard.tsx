/**
 * SupportConfluenceCard — composite support/resistance zones for the
 * Technical Analysis tab. Renders the /support-confluence endpoint:
 * method-weighted zones where gamma walls, volume profile, anchored VWAPs,
 * OI builds, moving averages, etc. agree.
 */
import { Alert, Box, Button, Chip, CircularProgress, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useSupportConfluence } from '../../hooks/useSecurities';
import type { SupportZone } from '../../api/securitiesTypes';

interface Props { ticker: string }

function SectionHeader({ title }: { title: string }) {
  return (
    <Typography variant="subtitle2" sx={{ color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
      {title}
    </Typography>
  );
}

function SectionError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert
      severity="error"
      action={
        <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={onRetry}>
          Retry
        </Button>
      }
      sx={{ fontSize: 12 }}
    >
      {message}
    </Alert>
  );
}

function contributorsLine(zone: SupportZone): string {
  return zone.contributors
    .map((c) => `${c.method}@${c.level.toFixed(2)}`)
    .join(', ');
}

function ZoneTable({ title, zones, color }: { title: string; zones: SupportZone[]; color: string }) {
  return (
    <Box data-testid={`confluence-zones-${title.toLowerCase()}`}>
      <Typography variant="caption" sx={{ color: '#6b7280' }}>{title}</Typography>
      {zones.length === 0 ? (
        <Typography variant="body2" color="text.secondary">No {title.toLowerCase()} zones within ±25% of price.</Typography>
      ) : (
        <Box sx={{ overflowX: 'auto', mt: 0.5 }}>
          <Table size="small" sx={{ '& td, & th': { fontSize: 11, py: 0.4 } }}>
            <TableHead>
              <TableRow>
                <TableCell>Zone</TableCell>
                <TableCell align="right">Distance</TableCell>
                <TableCell align="right">Score</TableCell>
                <TableCell align="center">Methods</TableCell>
                <TableCell>Contributors</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {zones.map((z, i) => (
                <TableRow key={i}>
                  <TableCell sx={{ color, fontWeight: 600, whiteSpace: 'nowrap' }}>
                    ${z.zone_low.toFixed(2)}–${z.zone_high.toFixed(2)}
                  </TableCell>
                  <TableCell align="right" sx={{ color: '#9ca3af' }}>
                    {z.distance_pct > 0 ? '+' : ''}{z.distance_pct.toFixed(1)}%
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600 }}>{z.score.toFixed(2)}</TableCell>
                  <TableCell align="center">
                    <Chip size="small" label={z.method_count}
                      sx={{ fontSize: 10, height: 18, bgcolor: color + '22', color, border: '1px solid', borderColor: color }} />
                  </TableCell>
                  <TableCell sx={{ color: '#6b7280', maxWidth: 320 }}>{contributorsLine(z)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
    </Box>
  );
}

export default function SupportConfluenceCard({ ticker }: Props) {
  const { data, isLoading, error, refetch } = useSupportConfluence(ticker);

  return (
    <Paper sx={{ p: 2 }} data-testid="support-confluence-card">
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap">
        <SectionHeader title="Support Confluence" />
        {data && !data.error && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="caption" sx={{ color: '#6b7280' }}>
              Price ${data.price.toFixed(2)}
            </Typography>
            {data.strongest_support && (
              <Chip
                size="small"
                data-testid="strongest-support-chip"
                label={`Strongest support $${data.strongest_support.center.toFixed(2)} (${data.strongest_support.distance_pct.toFixed(1)}%)`}
                sx={{
                  fontSize: 11, height: 20, fontWeight: 600, border: '1px solid',
                  bgcolor: '#1e3a2f',
                  color: Math.abs(data.strongest_support.distance_pct) <= 3 ? '#10b981' : '#9ca3af',
                  borderColor: Math.abs(data.strongest_support.distance_pct) <= 3 ? '#10b981' : '#4b5563',
                }}
              />
            )}
          </Stack>
        )}
      </Stack>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
      ) : error ? (
        <SectionError
          message={(error as Error).message ?? 'Failed to load support confluence'}
          onRetry={refetch}
        />
      ) : data?.error ? (
        <SectionError message={data.error} onRetry={refetch} />
      ) : data ? (
        <Stack spacing={2}>
          <ZoneTable title="Support" zones={data.support_zones} color="#10b981" />
          <ZoneTable title="Resistance" zones={data.resistance_zones} color="#ef4444" />

          {data.methods_failed.length > 0 && (
            <Alert severity="warning" sx={{ fontSize: 11 }} data-testid="confluence-methods-failed">
              Some level sources were unavailable: {data.methods_failed.join(', ')}
              <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={() => refetch()} sx={{ ml: 1 }}>
                Retry
              </Button>
            </Alert>
          )}

          {data.interpretation && (
            <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', fontStyle: 'italic', lineHeight: 1.5 }}>
              {data.interpretation}
            </Typography>
          )}
        </Stack>
      ) : null}
    </Paper>
  );
}
