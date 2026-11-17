/**
 * StopCell — the stop on a holding (#431 step 4.3). A row-bound fragment: it
 * shows only the stop its row was given, and is never shown on its own.
 */
import { Box, Chip, Tooltip, Typography } from '@mui/material';
import { formatCurrency } from '../../utils/formatting';
import { stopDetail, trailPercent } from './stopWords';
import type { Stop } from '../../api/stops';

interface Props {
  stop: Stop | null | undefined;
  /** The stop is in a paper book, where a reached stop sold the position. */
  paper?: boolean;
}

export default function StopCell({ stop, paper = false }: Props) {
  if (!stop) return <>—</>;
  if (stop.status === 'triggered') {
    return (
      <Tooltip title={stopDetail(stop, paper)}>
        <Chip
          size="small"
          label={`Reached ${formatCurrency(stop.trigger_price)}`}
          sx={{ bgcolor: 'var(--qc-tint-red)', color: 'var(--qc-ink-red)' }}
        />
      </Tooltip>
    );
  }
  return (
    <Tooltip title={stopDetail(stop, paper)}>
      <Box component="span" sx={{ whiteSpace: 'nowrap' }}>
        {formatCurrency(stop.stop_level)}
        {stop.kind === 'trailing' && (
          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
            trails {trailPercent(stop.trail_pct)}
          </Typography>
        )}
      </Box>
    </Tooltip>
  );
}
