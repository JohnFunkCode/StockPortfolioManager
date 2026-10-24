import { Chip, Typography } from '@mui/material';

// FinBERT sentiment palette, shared by the News column, the screener chips and
// the sentiment dashboard (moved from the retired Securities page, #224).
export const SENTIMENT_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  positive: { bg: 'var(--qc-tint-green)', text: '#10b981', border: '#10b981' },
  negative: { bg: 'var(--qc-tint-red)', text: '#ef4444', border: '#ef4444' },
  neutral:  { bg: 'var(--qc-surface)', text: 'var(--qc-muted)', border: 'var(--qc-dim)' },
};

export function SentimentBadge({ sentiment }: { sentiment: string | null | undefined }) {
  if (!sentiment) return <Typography variant="body2" sx={{ color: 'var(--qc-dim)' }}>—</Typography>;
  const c = SENTIMENT_COLORS[sentiment] ?? SENTIMENT_COLORS.neutral;
  const label = sentiment === 'positive' ? '↑ pos' : sentiment === 'negative' ? '↓ neg' : '– neu';
  return (
    <Chip size="small" label={label}
      sx={{ fontSize: 11, height: 20, bgcolor: c.bg, color: c.text, border: `1px solid ${c.border}` }} />
  );
}
