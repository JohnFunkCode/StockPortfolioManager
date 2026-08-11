/**
 * How trustworthy is everything below? (issue #147 Part D.)
 *
 * Every other panel on this page reads the same cache, and none of them can say
 * on their own whether a short list means "few good names" or "the warmer has
 * not been round yet". This banner makes that visible: per `data_type`, how
 * many symbols the cache holds and how old the oldest and newest entries are.
 *
 * It reports on the cache as a whole and takes no `scope` — it is inventory,
 * not a ranking, and narrowing it to the tracked roster would hide exactly the
 * case worth seeing (a cache full of one-off lookups and thin on the roster).
 *
 * A failure here is deliberately quiet: the banner disappears rather than
 * taking the page down, because it is the one panel whose absence costs
 * nothing analytically.
 */
import { Box, Chip, Skeleton, Stack, Tooltip, Typography } from '@mui/material';
import { useFundamentalsCacheStats } from '../../hooks/useFundamentals';
import { formatDate } from '../../utils/formatting';
import { MUTED } from './scoreLabels';

export default function CacheFreshnessBanner() {
  const { data, isLoading, error } = useFundamentalsCacheStats();

  if (isLoading) return <Skeleton variant="rounded" height={28} sx={{ mb: 2 }} />;
  // Silent on failure — see the module docstring.
  if (error || !data || data.error) return null;

  const types = data.data_types ?? [];
  if (types.length === 0) return null;

  return (
    <Stack
      direction="row"
      spacing={1}
      flexWrap="wrap"
      useFlexGap
      alignItems="center"
      sx={{ mb: 2 }}
      data-testid="cache-freshness-banner"
    >
      <Typography variant="caption" sx={{ color: MUTED }}>
        Cache:
      </Typography>
      {types.map((entry) => (
        <Tooltip
          key={entry.data_type}
          title={`oldest ${formatDate(entry.oldest)} · newest ${formatDate(entry.newest)}`}
        >
          <Chip
            size="small"
            variant="outlined"
            data-testid={`cache-chip-${entry.data_type}`}
            label={
              <Box component="span" sx={{ fontSize: 11 }}>
                {entry.data_type.replace(/_/g, ' ')} · {entry.symbol_count}
              </Box>
            }
            sx={{ height: 22, color: '#9ca3af' }}
          />
        </Tooltip>
      ))}
    </Stack>
  );
}
