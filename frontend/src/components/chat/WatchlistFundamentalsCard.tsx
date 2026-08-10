/**
 * Self-fetching chat card for the Sidekick — the shared watchlist ranked by
 * fundamental score (issue #147 Part C). No props ever: the watchlist is one
 * global list, so there is nothing for the model to name.
 *
 * Shares the ['watchlist-fundamentals'] query with the /watchlist page, so
 * rendering this next to the page costs one fetch, not two. Every number on it
 * arrives precomputed — including the ranking, which the server does — and the
 * card only slices the top of the list (Rule 8.4: no displayed math here).
 */
import { Alert, Box, Chip, CircularProgress, Stack, Tooltip, Typography } from '@mui/material';
import { useWatchlistFundamentals } from '../../hooks/useWatchlist';
import { formatPercentRaw } from '../../utils/formatting';

/** The side rail is narrow — the whole list belongs on the page. */
const TOP_N = 10;

const GREEN = '#10b981';
const RED = '#ef4444';

const LABEL_COLORS: Record<string, string> = {
  strong: GREEN,
  improving: '#34d399',
  mixed: '#9ca3af',
  weak: RED,
};

export default function WatchlistFundamentalsCard() {
  const { data, isLoading, error } = useWatchlistFundamentals();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Loading the watchlist…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t load the watchlist.</Alert>;
  }

  const rows = data?.rows ?? [];
  if (rows.length === 0) {
    // Deliberately not a quiet empty box: an empty watchlist is the condition
    // main.py raises a Discord alarm for.
    return <Alert severity="warning">The watchlist is empty.</Alert>;
  }

  const shown = rows.slice(0, TOP_N);

  return (
    <Box data-testid="watchlist-fundamentals-card">
      <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
        <Typography variant="subtitle2">Watchlist — top {shown.length}</Typography>
        <Typography variant="caption" color="text.secondary">
          of {data?.count ?? rows.length}
        </Typography>
        {(data?.stale_count ?? 0) > 0 && (
          <Tooltip title="Cached fundamentals past their TTL — shown, not dropped">
            <Chip size="small" label={`${data?.stale_count} stale`} sx={{ fontSize: 10, height: 18 }} />
          </Tooltip>
        )}
        {(data?.unscored_count ?? 0) > 0 && (
          <Tooltip title="No fundamentals cached yet — these sort to the bottom">
            <Chip
              size="small"
              variant="outlined"
              label={`${data?.unscored_count} unscored`}
              sx={{ fontSize: 10, height: 18 }}
            />
          </Tooltip>
        )}
      </Stack>

      <Box sx={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: '#6b7280', textAlign: 'left', borderBottom: '1px solid #1f2937' }}>
              <th style={{ padding: '3px 6px' }}>Symbol</th>
              <th style={{ padding: '3px 6px' }}>Score</th>
              <th style={{ padding: '3px 6px', textAlign: 'right' }}>30d</th>
              <th style={{ padding: '3px 6px', textAlign: 'right' }}>YTD</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr key={row.symbol} style={{ borderBottom: '1px solid #111827' }}>
                <td style={{ padding: '3px 6px', color: '#818cf8', fontWeight: 600 }}>
                  {row.symbol}
                </td>
                <td
                  style={{
                    padding: '3px 6px',
                    color: LABEL_COLORS[row.fundamental_label ?? 'mixed'] ?? '#9ca3af',
                    fontWeight: 600,
                  }}
                >
                  {row.composite_score == null ? '—' : row.composite_score.toFixed(0)}
                </td>
                <td
                  style={{
                    padding: '3px 6px',
                    textAlign: 'right',
                    color: row.return_30d == null ? '#4b5563' : row.return_30d >= 0 ? GREEN : RED,
                  }}
                >
                  {row.return_30d == null ? '—' : formatPercentRaw(row.return_30d, 1)}
                </td>
                <td
                  style={{
                    padding: '3px 6px',
                    textAlign: 'right',
                    color: row.return_ytd == null ? '#4b5563' : row.return_ytd >= 0 ? GREEN : RED,
                  }}
                >
                  {row.return_ytd == null ? '—' : formatPercentRaw(row.return_ytd, 1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Box>
    </Box>
  );
}
