/**
 * Score movers over a 90-day window (issue #147 Part D).
 *
 * This is the panel a static nightly report could not express: it needs two
 * snapshots of the same symbol and a diff between them, which is a thing you
 * have only if you kept the history. The server computes every delta and the
 * direction label; nothing here subtracts.
 *
 * `symbols_with_insufficient_history` is shown rather than hidden. A symbol the
 * warmer has only reached once is not a non-mover — it is unmeasured, and an
 * empty list with a large count means "the cache is young", not "nothing
 * changed".
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
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import { Link as RouterLink } from 'react-router-dom';
import { useScoreChanges } from '../../hooks/useFundamentals';
import { formatDate } from '../../utils/formatting';
import { DIM, GREEN, MUTED, RED } from './scoreLabels';

export interface ScoreChangesPanelProps {
  variant?: 'page' | 'rail';
}

const RAIL_LIMIT = 8;

export default function ScoreChangesPanel({ variant = 'page' }: ScoreChangesPanelProps) {
  const rail = variant === 'rail';
  const { data, isLoading, error } = useScoreChanges();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Comparing snapshots…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load fundamental score changes.</Alert>;
  }

  const changes = data?.changes ?? [];
  // The response is already sorted by delta, improvers first — so the rail's
  // slice is the biggest upgrades, and taking the tail would be the downgrades.
  const shown = rail ? changes.slice(0, RAIL_LIMIT) : changes;

  const header = (
    <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
      <Typography variant={rail ? 'subtitle2' : 'h6'}>
        Score changes · {data?.since_days ?? 90}d
      </Typography>
      <Tooltip title={`Moves of at least ${data?.min_delta ?? 2} points`}>
        <Chip
          size="small"
          variant="outlined"
          label={`${changes.length} movers`}
          sx={{ fontSize: 11, height: 20, color: MUTED }}
        />
      </Tooltip>
      {(data?.symbols_with_insufficient_history ?? 0) > 0 && (
        <Tooltip title="Fewer than two cached snapshots in the window — unmeasured, not unchanged">
          <Chip
            size="small"
            variant="outlined"
            label={`${data?.symbols_with_insufficient_history} unmeasured`}
            sx={{ fontSize: 11, height: 20, color: MUTED }}
          />
        </Tooltip>
      )}
    </Stack>
  );

  const body =
    shown.length === 0 ? (
      <Alert severity="info">
        No symbol moved {data?.min_delta ?? 2} points or more in the last {data?.since_days ?? 90}{' '}
        days.
      </Alert>
    ) : (
      <Box sx={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: rail ? 12 : 13 }}>
          <thead>
            <tr style={{ color: MUTED, textAlign: 'left', borderBottom: '1px solid #1f2937' }}>
              <th style={{ padding: '4px 6px' }}>Symbol</th>
              <th style={{ padding: '4px 6px', textAlign: 'right' }}>Δ</th>
              <th style={{ padding: '4px 6px', textAlign: 'right' }}>Then → now</th>
              {!rail && <th style={{ padding: '4px 6px' }}>Label</th>}
              {!rail && <th style={{ padding: '4px 6px' }}>Window</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((change) => {
              const up = change.direction === 'improving';
              const color = up ? GREEN : RED;
              return (
                <tr key={change.symbol} style={{ borderBottom: '1px solid #111827' }}>
                  <td style={{ padding: '4px 6px', fontWeight: 600 }}>
                    <RouterLink
                      to={`/securities/${change.symbol}`}
                      style={{ color: '#818cf8', textDecoration: 'none' }}
                    >
                      {change.symbol}
                    </RouterLink>
                  </td>
                  <td style={{ padding: '4px 6px', textAlign: 'right', color, fontWeight: 600 }}>
                    <Stack direction="row" spacing={0.25} justifyContent="flex-end" alignItems="center">
                      {up ? (
                        <ArrowUpwardIcon sx={{ fontSize: 12 }} aria-label="improving" />
                      ) : (
                        <ArrowDownwardIcon sx={{ fontSize: 12 }} aria-label="deteriorating" />
                      )}
                      <span>{Math.abs(change.delta).toFixed(0)}</span>
                    </Stack>
                  </td>
                  <td style={{ padding: '4px 6px', textAlign: 'right', color: '#d1d5db' }}>
                    {change.score_then.toFixed(0)} → {change.score_now.toFixed(0)}
                  </td>
                  {!rail && (
                    <td style={{ padding: '4px 6px', color: MUTED }}>
                      {change.label_then ?? '—'} → {change.label_now ?? '—'}
                    </td>
                  )}
                  {!rail && (
                    <td style={{ padding: '4px 6px', color: DIM }}>
                      {formatDate(change.first_snapshot)} – {formatDate(change.last_snapshot)}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Box>
    );

  if (rail) {
    return (
      <Box data-testid="score-changes-panel">
        {header}
        {body}
      </Box>
    );
  }

  return (
    <Paper sx={{ p: 2 }} data-testid="score-changes-panel">
      {header}
      {body}
    </Paper>
  );
}
