/**
 * The 1d cell shared by the Portfolio and Watchlist tables (issue #518).
 *
 * Every number here is chosen and computed server-side: `value` is
 * `return_1d_display` (the quote's regular-session change when the quote is
 * fresh, otherwise the cached close-to-close return), and `stale` is
 * `return_1d_stale`. This component only renders them, plus the pre-market or
 * after-hours change when that session is the current or most recent one.
 */
import { Box, Tooltip, Typography } from '@mui/material';
import { formatPercentRaw } from '../../utils/formatting';

const GREEN = '#10b981';
const RED = '#ef4444';

export interface OneDayFields {
  return_1d_display?: number | null;
  return_1d_stale?: boolean | null;
  market_state?: string | null;
  pre_change_pct?: number | null;
  post_change_pct?: number | null;
}

export interface SessionBadge {
  label: 'Pre' | 'AH';
  title: string;
  value: number;
}

/** Which extended-session change to show beside 1d, if any.
 *
 * Yahoo's `market_state` is per symbol, so a foreign listing gets its own
 * session. Pre-market states show the pre-market change; after the close
 * (POST, POSTPOST, CLOSED) the after-hours change; REGULAR shows neither. */
export function sessionBadge(
  marketState: string | null | undefined,
  pre: number | null | undefined,
  post: number | null | undefined,
): SessionBadge | null {
  const state = (marketState ?? '').toUpperCase();
  if (state.startsWith('PRE') && pre != null) {
    return { label: 'Pre', title: 'Pre-market change', value: pre };
  }
  if ((state.startsWith('POST') || state === 'CLOSED') && post != null) {
    return { label: 'AH', title: 'After-hours change', value: post };
  }
  return null;
}

/** Sort 1d values with stale rows last in **both** directions, then nulls.
 *
 * A stale figure is yesterday's move, so ranking it among today's would put an
 * old number at the top of "biggest movers". `sign` is applied to the numbers
 * only; the stale and null ordering sits outside it. */
export function compareOneDay(
  a: OneDayFields,
  b: OneDayFields,
  direction: 'asc' | 'desc' | null | undefined,
): number {
  const rank = (row: OneDayFields) =>
    row.return_1d_display == null ? 2 : row.return_1d_stale ? 1 : 0;
  const byRank = rank(a) - rank(b);
  if (byRank !== 0) return byRank;
  if (a.return_1d_display == null || b.return_1d_display == null) return 0;
  const sign = direction === 'desc' ? -1 : 1;
  return sign * (a.return_1d_display - b.return_1d_display);
}

function signed(value: number, decimals: number): string {
  return `${value > 0 ? '+' : ''}${formatPercentRaw(value, decimals)}`;
}

export default function OneDayChange({
  row,
  decimals = 2,
}: {
  row: OneDayFields;
  decimals?: number;
}) {
  const value = row.return_1d_display;
  if (value == null) {
    return (
      <Typography variant="body2" component="span" sx={{ color: 'var(--qc-dim)' }}>
        —
      </Typography>
    );
  }
  const badge = sessionBadge(row.market_state, row.pre_change_pct, row.post_change_pct);
  return (
    <Box
      component="span"
      sx={{ display: 'inline-flex', alignItems: 'baseline', gap: 0.5, whiteSpace: 'nowrap' }}
    >
      <Typography
        variant="body2"
        component="span"
        sx={{ color: value >= 0 ? GREEN : RED, fontWeight: 500 }}
      >
        {formatPercentRaw(value, decimals)}
      </Typography>
      {row.return_1d_stale && (
        <Tooltip title="No live quote. This is the last cached daily change.">
          <Typography
            variant="caption"
            component="span"
            aria-label="stale"
            sx={{ color: 'var(--qc-ink-amber)', fontWeight: 600 }}
          >
            stale
          </Typography>
        </Tooltip>
      )}
      {badge && (
        <Tooltip title={badge.title}>
          <Typography
            variant="caption"
            component="span"
            data-testid="session-badge"
            sx={{ color: 'var(--qc-muted)' }}
          >
            {badge.label} {signed(badge.value, decimals)}
          </Typography>
        </Tooltip>
      )}
    </Box>
  );
}
