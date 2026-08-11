/**
 * The same ranking cut by sector (issue #147 Part D) — one card per sector with
 * its best names, so "the tracked list is strong" can be read as "strong
 * *where*".
 *
 * The server returns `sectors` already grouped, sorted and truncated to
 * `top_n`; iteration order here comes from `Object.entries`, which preserves
 * the insertion order the server built alphabetically. Symbols with no sector
 * on file arrive under `"Unknown"` and are rendered rather than dropped — that
 * bucket's size is the useful signal about how complete the cache is.
 */
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { useSectorBreakdown } from '../../hooks/useFundamentals';
import { labelColor, MUTED } from './scoreLabels';

export default function SectorBreakdownPanel() {
  const { data, isLoading, error } = useSectorBreakdown();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Grouping by sector…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load the sector breakdown.</Alert>;
  }

  const sectors = Object.entries(data?.sectors ?? {});

  return (
    <Paper sx={{ p: 2 }} data-testid="sector-breakdown-panel">
      <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
        <Typography variant="h6">By sector</Typography>
        <Chip
          size="small"
          variant="outlined"
          label={`${data?.sector_count ?? 0} sectors · ${data?.total_symbols ?? 0} tracked`}
          sx={{ fontSize: 11, height: 20, color: MUTED }}
        />
      </Stack>

      {sectors.length === 0 ? (
        <Alert severity="info">No sectors to show — nothing tracked has a cached score yet.</Alert>
      ) : (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: '1fr 1fr 1fr' },
            gap: 1.5,
          }}
        >
          {sectors.map(([sector, entries]) => (
            <Box
              key={sector}
              data-testid={`sector-card-${sector}`}
              sx={{ border: '1px solid #1f2937', borderRadius: 1, p: 1.25 }}
            >
              <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                {sector}
              </Typography>
              <Stack spacing={0.5}>
                {entries.map((entry) => (
                  <Stack
                    key={entry.symbol}
                    direction="row"
                    justifyContent="space-between"
                    alignItems="center"
                  >
                    <RouterLink
                      to={`/securities/${entry.symbol}`}
                      style={{ color: '#818cf8', textDecoration: 'none', fontSize: 13, fontWeight: 600 }}
                    >
                      {entry.symbol}
                    </RouterLink>
                    <Typography
                      variant="caption"
                      sx={{ color: labelColor(entry.fundamental_label), fontWeight: 600 }}
                    >
                      {entry.composite_score == null ? '—' : entry.composite_score.toFixed(0)}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
}
