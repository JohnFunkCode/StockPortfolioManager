/**
 * Small pieces every Signals-tab panel shares: badges, metric rows, the
 * loading and error states, and the colour lookups for signal words.
 */
import type { ReactNode } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, Stack, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { tint } from '../../themes';

export const GREEN = '#10b981';
export const RED = '#ef4444';
export const AMBER = '#f59e0b';

export function SignalBadge({ label, color }: { label: string; color: string }) {
  return (
    <Chip size="small" label={label}
      sx={{ fontSize: 11, height: 20, bgcolor: tint(color), color, borderColor: color, border: '1px solid' }} />
  );
}

export function MetricRow({ label, value, color = 'var(--qc-text)' }: { label: string; value: ReactNode; color?: string }) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ py: 0.4 }}>
      <Typography variant="body2" sx={{ color: 'var(--qc-faint)', fontSize: 12 }}>{label}</Typography>
      <Typography variant="body2" sx={{ color, fontWeight: 600, fontSize: 12 }}>{value}</Typography>
    </Stack>
  );
}

export function SectionError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert
      severity="error"
      action={
        <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={onRetry}>
          Retry
        </Button>
      }
      sx={{ fontSize: 12 }}
    >
      {message}
    </Alert>
  );
}

/** A panel body's three states: spinner, error with Retry, or the content. */
export function SectionBody({
  loading, error, fallback, onRetry, children,
}: {
  loading: boolean;
  error: unknown;
  fallback: string;
  onRetry: () => void;
  children: ReactNode;
}) {
  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>;
  }
  if (error) {
    return <SectionError message={(error as Error).message ?? fallback} onRetry={onRetry} />;
  }
  return <>{children}</>;
}

/** The partial-failure warning a signals response carries in `_errors`. */
export function PartialErrors({
  errors, prefix, onRetry, sx,
}: {
  errors: Record<string, unknown> | null | undefined;
  prefix: string;
  onRetry: () => void;
  sx?: object;
}) {
  if (!errors || Object.keys(errors).length === 0) return null;
  return (
    <Alert severity="warning" sx={{ fontSize: 11, ...sx }}>
      {prefix}: {Object.keys(errors).join(', ')}
      <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={onRetry} sx={{ ml: 1 }}>
        Retry
      </Button>
    </Alert>
  );
}

export function NoData() {
  return <Typography variant="body2" color="text.secondary">—</Typography>;
}

export function BlockTitle({ children }: { children: ReactNode }) {
  return <Typography variant="caption" sx={{ color: 'var(--qc-faint)' }}>{children}</Typography>;
}

export function Note({ children, sx }: { children: ReactNode; sx?: object }) {
  return (
    <Typography variant="caption" sx={{ color: 'var(--qc-muted)', display: 'block', fontStyle: 'italic', ...sx }}>
      {children}
    </Typography>
  );
}

const STRENGTH: Record<string, string> = {
  strong: GREEN,
  moderate: AMBER,
  weak: '#f97316',
  none: 'var(--qc-faint)',
};

export function strengthColor(s: string | undefined): string {
  if (!s) return 'var(--qc-faint)';
  return STRENGTH[s] ?? 'var(--qc-muted)';
}

// First match wins, in this order, as the original if-chain did.
const SIGNAL_WORDS: Array<[string[], string]> = [
  [['bull', 'strong', 'above', 'rising', 'buy'], GREEN],
  [['bear', 'below', 'sell', 'falling', 'overbought'], RED],
  [['neutral', 'none', 'flat'], 'var(--qc-faint)'],
];

export function signalColor(s: string | undefined): string {
  if (!s) return 'var(--qc-faint)';
  const sl = s.toLowerCase();
  const hit = SIGNAL_WORDS.find(([words]) => words.some((w) => sl.includes(w)));
  return hit ? hit[1] : AMBER;
}

/** "+1.2" / "-0.4": the sign the old inline ternaries added. */
export function signedFixed(n: number, digits = 1): string {
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`;
}

// Bias chips (candlestick bias, news tone): ink on tint, per themes.ts.
const BIAS: Record<string, { bg: string; fg: string }> = {
  bullish: { bg: 'var(--qc-tint-green)', fg: 'var(--qc-ink-green)' },
  bearish: { bg: 'var(--qc-tint-red)', fg: 'var(--qc-ink-red)' },
};

export function biasStyle(bias: string): { bg: string; fg: string } {
  return BIAS[bias] ?? { bg: 'var(--qc-surface)', fg: 'var(--qc-muted)' };
}
