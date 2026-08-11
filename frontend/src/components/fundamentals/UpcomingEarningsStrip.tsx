/**
 * The next two weeks of earnings across the tracked universe (issue #147
 * Part D) — a strip of date chips rather than a table, because the only
 * question it answers is "what is about to move?".
 *
 * Stale cache rows are excluded by the server's default: an earnings date read
 * weeks ago may since have been rescheduled, and a confidently wrong date is
 * worse than a gap. `stale_excluded` is surfaced so the gap is visible — a
 * quiet calendar and an unwarmed cache look identical otherwise.
 */
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { useUpcomingEarnings } from '../../hooks/useFundamentals';
import { formatDate } from '../../utils/formatting';
import { MUTED } from './scoreLabels';

/** Server-supplied risk labels, coloured. Anything unrecognized stays neutral
 * rather than being bucketed by guesswork. */
const RISK_COLORS: Record<string, string> = {
  high: '#ef4444',
  elevated: '#f59e0b',
  moderate: '#9ca3af',
  low: '#10b981',
};

export default function UpcomingEarningsStrip() {
  const { data, isLoading, error } = useUpcomingEarnings();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Reading the earnings calendar…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load upcoming earnings.</Alert>;
  }

  const upcoming = data?.upcoming ?? [];

  return (
    <Paper sx={{ p: 2 }} data-testid="upcoming-earnings-strip">
      <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
        <Typography variant="h6">Earnings · next {data?.days_window ?? 14}d</Typography>
        <Chip
          size="small"
          variant="outlined"
          label={`${data?.count ?? 0} scheduled`}
          sx={{ fontSize: 11, height: 20, color: MUTED }}
        />
        {(data?.stale_excluded ?? 0) > 0 && (
          <Tooltip title="Cached dates past their TTL, hidden because a rescheduled date would be shown as fact">
            <Chip
              size="small"
              label={`${data?.stale_excluded} stale hidden`}
              sx={{
                fontSize: 11,
                height: 20,
                bgcolor: '#3a2e1e',
                color: '#f59e0b',
                border: '1px solid #f59e0b',
              }}
            />
          </Tooltip>
        )}
      </Stack>

      {upcoming.length === 0 ? (
        <Alert severity="info">
          Nothing on the tracked list reports in the next {data?.days_window ?? 14} days.
        </Alert>
      ) : (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {upcoming.map((row) => (
            <Box
              key={`${row.symbol}-${row.earnings_date}`}
              data-testid={`earnings-${row.symbol}`}
              sx={{
                border: '1px solid #1f2937',
                borderRadius: 1,
                px: 1.25,
                py: 0.75,
                minWidth: 108,
              }}
            >
              <RouterLink
                to={`/securities/${row.symbol}`}
                style={{ color: '#818cf8', textDecoration: 'none', fontSize: 13, fontWeight: 600 }}
              >
                {row.symbol}
              </RouterLink>
              <Typography variant="caption" sx={{ display: 'block', color: '#d1d5db' }}>
                {formatDate(row.earnings_date)}
              </Typography>
              <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 0.25 }}>
                <Typography variant="caption" sx={{ color: MUTED }}>
                  {row.days_to_earnings === 0 ? 'today' : `in ${row.days_to_earnings}d`}
                </Typography>
                {row.risk_level && (
                  <Typography
                    variant="caption"
                    sx={{ color: RISK_COLORS[row.risk_level.toLowerCase()] ?? MUTED }}
                  >
                    · {row.risk_level}
                  </Typography>
                )}
              </Stack>
            </Box>
          ))}
        </Stack>
      )}
    </Paper>
  );
}
