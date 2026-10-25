/**
 * Signals tab, section 2: Price Structure — higher lows, gaps and candlestick
 * patterns from `/signals/technical`. Split out of SignalsTab (#439 follow-up).
 */
import { Box, Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useTechnicalSignals } from '../../hooks/useSecurities';
import type { TechnicalSignalsResponse } from '../../api/securitiesTypes';
import SectionHeader from './SectionHeader';
import {
  AMBER, BlockTitle, MetricRow, NoData, Note, SectionBody, SignalBadge, biasStyle, signedFixed,
  strengthColor,
} from './signalsShared';

type Tech = TechnicalSignalsResponse;

function HigherLowsBlock({ lows }: { lows: Tech['higher_lows'] | undefined }) {
  if (!lows) {
    return <Box sx={{ flex: 1 }}><BlockTitle>Higher Lows (Daily)</BlockTitle><NoData /></Box>;
  }
  const label = lows.higher_low_pattern
    ? `${lows.pattern_strength} — ${lows.consecutive_higher_lows} consecutive`
    : 'No pattern';
  const color = lows.higher_low_pattern ? strengthColor(lows.pattern_strength) : 'var(--qc-faint)';
  return (
    <Box sx={{ flex: 1 }}>
      <BlockTitle>Higher Lows (Daily)</BlockTitle>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
        <SignalBadge label={label} color={color} />
      </Stack>
      <Note sx={{ mt: 0.5 }}>{lows.interpretation}</Note>
    </Box>
  );
}

function GapBlock({ gaps }: { gaps: Tech['gap_analysis'] | undefined }) {
  if (!gaps) {
    return <Box sx={{ flex: 1 }}><BlockTitle>Gap Analysis (60d)</BlockTitle><NoData /></Box>;
  }
  return (
    <Box sx={{ flex: 1 }}>
      <BlockTitle>Gap Analysis (60d)</BlockTitle>
      <MetricRow label="Unfilled gaps" value={gaps.unfilled_count} />
      <MetricRow label="Partial fills" value={gaps.partial_count} />
      {gaps.bounce_targets.length > 0 && (
        <Box sx={{ mt: 0.5 }}>
          {gaps.bounce_targets.map((t, i) => (
            <Typography key={i} variant="caption" sx={{ color: AMBER, display: 'block', fontSize: 11 }}>
              {t.direction === 'gap_up' ? '↑' : '↓'} ${t.gap_bottom.toFixed(2)}–${t.gap_top.toFixed(2)}
              {' '}({signedFixed(t.distance_pct)}%)
            </Typography>
          ))}
        </Box>
      )}
      <Note sx={{ mt: 0.5 }}>{gaps.interpretation}</Note>
    </Box>
  );
}

type Pattern = NonNullable<Tech['candlestick_patterns']>['patterns_found'][number];

function PatternRow({ p }: { p: Pattern }) {
  const bias = biasStyle(p.bias);
  return (
    <TableRow>
      <TableCell>{p.date}</TableCell>
      <TableCell sx={{ fontFamily: 'monospace' }}>{p.pattern}</TableCell>
      <TableCell>
        <Chip size="small" label={p.bias} sx={{ fontSize: 10, height: 18, bgcolor: bias.bg, color: bias.fg }} />
      </TableCell>
      <TableCell>
        <SignalBadge label={p.strength} color={strengthColor(p.strength)} />
      </TableCell>
      <TableCell sx={{ color: 'var(--qc-muted)', maxWidth: 200 }}>{p.notes.join('; ')}</TableCell>
    </TableRow>
  );
}

function CandlestickBlock({ candles }: { candles: Tech['candlestick_patterns'] | undefined }) {
  if (!candles || candles.pattern_count <= 0) return null;
  return (
    <Box>
      <BlockTitle>Candlestick Patterns (last 10 bars) — {candles.bounce_signal}</BlockTitle>
      <Box sx={{ overflowX: 'auto', mt: 0.5 }}>
        <Table size="small" sx={{ '& td, & th': { fontSize: 11, py: 0.4 } }}>
          <TableHead>
            <TableRow>
              <TableCell>Date</TableCell>
              <TableCell>Pattern</TableCell>
              <TableCell>Bias</TableCell>
              <TableCell>Strength</TableCell>
              <TableCell>Notes</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {candles.patterns_found.slice(0, 5).map((p, i) => <PatternRow key={i} p={p} />)}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}

export default function StructurePanel({ ticker }: { ticker: string }) {
  const { data: tech, isLoading, error, refetch } = useTechnicalSignals(ticker, true);
  return (
    <Paper sx={{ p: 2 }}>
      <SectionHeader title="Price Structure" />
      <SectionBody loading={isLoading} error={error} fallback="Failed to load technical signals" onRetry={refetch}>
        <Stack spacing={2}>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <HigherLowsBlock lows={tech?.higher_lows} />
            <GapBlock gaps={tech?.gap_analysis} />
          </Stack>
          <CandlestickBlock candles={tech?.candlestick_patterns} />
        </Stack>
      </SectionBody>
    </Paper>
  );
}
