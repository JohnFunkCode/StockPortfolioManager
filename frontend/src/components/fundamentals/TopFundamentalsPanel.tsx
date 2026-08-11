/**
 * "What's good?" — the tracked universe ranked by composite score
 * (issue #147 Part D).
 *
 * Everything displayed arrives ranked and labelled from
 * `GET /api/securities/fundamentals/top`; this component slices and paints. The
 * one number worth reading twice is the header's `eligible_count` against
 * `total_in_cache`: the gap is the warming backlog, and without it a short list
 * looks like a small universe rather than an unfinished one.
 *
 * `variant="rail"` is the sidekick's rendering — fewer rows, no coverage
 * column. Same fetch, same query key, so the page and the chat card share one
 * request.
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
import { useTopFundamentals } from '../../hooks/useFundamentals';
import { DIM, formatCoverage, labelColor, MUTED } from './scoreLabels';

export interface TopFundamentalsPanelProps {
  /** `'rail'` is the narrow sidekick rendering. */
  variant?: 'page' | 'rail';
}

const PAGE_N = 25;
const RAIL_N = 10;

export default function TopFundamentalsPanel({ variant = 'page' }: TopFundamentalsPanelProps) {
  const rail = variant === 'rail';
  // Ask for what is shown. The rail's shorter list is a different query key,
  // which is correct — the server does the ranking, so a client-side slice of
  // the page's 25 would be the same answer only by coincidence of `n`.
  const { data, isLoading, error } = useTopFundamentals({ n: rail ? RAIL_N : PAGE_N });

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Ranking the tracked universe…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load the fundamental rankings.</Alert>;
  }

  const rankings = data?.rankings ?? [];

  const header = (
    <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
      <Typography variant={rail ? 'subtitle2' : 'h6'}>Top by fundamental score</Typography>
      <Tooltip
        title={`${data?.eligible_count ?? 0} of ${data?.total_in_cache ?? 0} tracked symbols have a cached score above ${formatCoverage(data?.min_coverage)} coverage`}
      >
        <Chip
          size="small"
          label={`${data?.eligible_count ?? 0} scored / ${data?.total_in_cache ?? 0} tracked`}
          variant="outlined"
          sx={{ fontSize: 11, height: 20, color: MUTED }}
        />
      </Tooltip>
    </Stack>
  );

  if (rankings.length === 0) {
    return (
      <Box data-testid="top-fundamentals-panel">
        {header}
        <Alert severity="info">
          Nothing on the tracked list has a cached score yet — the nightly warmer converges over a
          few nights.
        </Alert>
      </Box>
    );
  }

  const table = (
    <Box sx={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: rail ? 12 : 13 }}>
        <thead>
          <tr style={{ color: MUTED, textAlign: 'left', borderBottom: '1px solid #1f2937' }}>
            <th style={{ padding: '4px 6px', width: 32 }}>#</th>
            <th style={{ padding: '4px 6px' }}>Symbol</th>
            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Score</th>
            <th style={{ padding: '4px 6px' }}>Label</th>
            {!rail && <th style={{ padding: '4px 6px', textAlign: 'right' }}>Coverage</th>}
          </tr>
        </thead>
        <tbody>
          {rankings.map((row) => (
            <tr key={row.symbol} style={{ borderBottom: '1px solid #111827' }}>
              <td style={{ padding: '4px 6px', color: DIM }}>{row.rank}</td>
              <td style={{ padding: '4px 6px', fontWeight: 600 }}>
                <RouterLink
                  to={`/securities/${row.symbol}`}
                  style={{ color: '#818cf8', textDecoration: 'none' }}
                >
                  {row.symbol}
                </RouterLink>
              </td>
              <td
                style={{
                  padding: '4px 6px',
                  textAlign: 'right',
                  color: labelColor(row.fundamental_label),
                  fontWeight: 600,
                }}
              >
                {row.composite_score == null ? '—' : row.composite_score.toFixed(0)}
              </td>
              <td style={{ padding: '4px 6px', color: labelColor(row.fundamental_label) }}>
                {row.fundamental_label ?? '—'}
              </td>
              {!rail && (
                <td style={{ padding: '4px 6px', textAlign: 'right', color: MUTED }}>
                  {formatCoverage(row.coverage)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </Box>
  );

  if (rail) {
    return (
      <Box data-testid="top-fundamentals-panel">
        {header}
        {table}
      </Box>
    );
  }

  return (
    <Paper sx={{ p: 2 }} data-testid="top-fundamentals-panel">
      {header}
      {table}
    </Paper>
  );
}
