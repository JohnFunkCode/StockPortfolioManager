import { useNavigate } from 'react-router-dom';
import { Box, Button, Chip, CircularProgress, Collapse, Paper, Stack, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import type { SentimentSummaryItem, BulkSentimentResponse } from '../../api/securitiesTypes';
import { SENTIMENT_COLORS } from './sentiment';

interface Props {
  open: boolean;
  data: BulkSentimentResponse | undefined;
  isLoading: boolean;
  onRefresh: () => void;
}

/**
 * FinBERT sentiment per security. The page owns the query (its News column reads
 * the same summary), so this component only renders it.
 */
export default function SentimentDashboard({ open, data, isLoading, onRefresh }: Props) {
  const navigate = useNavigate();
  return (
    <Collapse in={open}>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
          <Typography variant="subtitle2">News Sentiment Dashboard (FinBERT)</Typography>
          <Button size="small" startIcon={<RefreshIcon />} onClick={onRefresh} disabled={isLoading}>
            Refresh
          </Button>
        </Stack>

        {isLoading && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <CircularProgress size={18} />
            <Typography variant="body2" color="text.secondary">Loading sentiment data…</Typography>
          </Box>
        )}

        {!isLoading && data && data.count === 0 && (
          <Typography variant="body2" color="text.secondary">
            No sentiment data yet — open a security's Signals tab to score its news.
          </Typography>
        )}

        {!isLoading && data && data.count > 0 && (
          <>
            <Stack direction="row" spacing={2} sx={{ mb: 1.5 }}>
              {(['negative', 'neutral', 'positive'] as const).map((s) => {
                const count = data.items.filter((i) => i.overall_sentiment === s).length;
                const c = SENTIMENT_COLORS[s];
                return (
                  <Chip key={s} size="small"
                    label={`${s === 'positive' ? '↑' : s === 'negative' ? '↓' : '–'} ${count} ${s}`}
                    sx={{ fontSize: 11, bgcolor: c.bg, color: c.text, border: `1px solid ${c.border}` }} />
                );
              })}
              <Typography variant="caption" sx={{ color: 'var(--qc-faint)', alignSelf: 'center' }}>
                {data.count} scored securities
              </Typography>
            </Stack>

            <Box sx={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--qc-faint)', textAlign: 'left', borderBottom: '1px solid var(--qc-surface)' }}>
                    <th style={{ padding: '4px 8px' }}>Symbol</th>
                    <th style={{ padding: '4px 8px' }}>Name</th>
                    <th style={{ padding: '4px 8px' }}>Sentiment</th>
                    <th style={{ padding: '4px 8px', textAlign: 'center' }}>↑</th>
                    <th style={{ padding: '4px 8px', textAlign: 'center' }}>↓</th>
                    <th style={{ padding: '4px 8px', textAlign: 'center' }}>–</th>
                    <th style={{ padding: '4px 8px' }}>Scored</th>
                    <th style={{ padding: '4px 8px' }}>As of</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item: SentimentSummaryItem) => {
                    const c = SENTIMENT_COLORS[item.overall_sentiment ?? 'neutral'];
                    return (
                      <tr key={item.symbol}
                        style={{ borderBottom: '1px solid var(--qc-surface-sunk)', cursor: 'pointer' }}
                        onClick={() => navigate(`/securities/${item.symbol}`)}
                      >
                        <td style={{ padding: '4px 8px', color: '#818cf8', fontWeight: 600 }}>{item.symbol}</td>
                        <td style={{ padding: '4px 8px', color: 'var(--qc-text-soft)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</td>
                        <td style={{ padding: '4px 8px' }}>
                          <span style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}`, borderRadius: 4, padding: '1px 6px', fontSize: 11 }}>
                            {item.overall_sentiment ?? '—'}
                          </span>
                        </td>
                        <td style={{ padding: '4px 8px', textAlign: 'center', color: '#10b981' }}>{item.positive_count}</td>
                        <td style={{ padding: '4px 8px', textAlign: 'center', color: '#ef4444' }}>{item.negative_count}</td>
                        <td style={{ padding: '4px 8px', textAlign: 'center', color: 'var(--qc-faint)' }}>{item.neutral_count}</td>
                        <td style={{ padding: '4px 8px', color: 'var(--qc-muted)' }}>{item.scored_count}</td>
                        <td style={{ padding: '4px 8px', color: 'var(--qc-dim)' }}>{item.captured_at?.slice(0, 10) ?? '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Box>
          </>
        )}
      </Paper>
    </Collapse>
  );
}
