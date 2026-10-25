/**
 * Signals tab, section 1: Momentum & Volume — stochastic, VWAP, OBV and volume
 * analysis from `/signals/technical`. Split out of SignalsTab (#439 follow-up).
 */
import { Box, Chip, Paper, Stack, Typography } from '@mui/material';
import { useTechnicalSignals } from '../../hooks/useSecurities';
import type { TechnicalSignalsResponse } from '../../api/securitiesTypes';
import SectionHeader from './SectionHeader';
import {
  AMBER, BlockTitle, GREEN, MetricRow, NoData, Note, PartialErrors, RED, SectionBody,
  SignalBadge, signalColor, signedFixed, strengthColor,
} from './signalsShared';

type Tech = TechnicalSignalsResponse;

function kColor(k: number): string {
  if (k >= 80) return RED;
  if (k <= 20) return GREEN;
  return 'var(--qc-text)';
}

function StochasticBlock({ stoch }: { stoch: Tech['stochastic'] | undefined }) {
  return (
    <Box sx={{ minWidth: 170, flex: 1 }}>
      <BlockTitle>Stochastic Oscillator</BlockTitle>
      {stoch ? (
        <>
          <MetricRow label="%K" value={stoch.k.toFixed(1)} color={kColor(stoch.k)} />
          <MetricRow label="%D" value={stoch.d.toFixed(1)} color="var(--qc-muted)" />
          <SignalBadge label={stoch.signal} color={signalColor(stoch.signal)} />
        </>
      ) : <NoData />}
    </Box>
  );
}

function VwapBlock({ vwap }: { vwap: Tech['vwap'] | undefined }) {
  if (!vwap) {
    return <Box sx={{ minWidth: 170, flex: 1 }}><BlockTitle>VWAP (20-day)</BlockTitle><NoData /></Box>;
  }
  const above = vwap.position === 'above_vwap';
  return (
    <Box sx={{ minWidth: 170, flex: 1 }}>
      <BlockTitle>VWAP (20-day)</BlockTitle>
      <MetricRow label="VWAP" value={`$${vwap.vwap.toFixed(2)}`} />
      <MetricRow label="Position" value={above ? 'Above' : 'Below'} color={above ? GREEN : RED} />
      <MetricRow label="Distance" value={`${signedFixed(vwap.distance_pct)}%`}
        color={Math.abs(vwap.distance_pct) > 5 ? AMBER : 'var(--qc-muted)'} />
      {vwap.reclaim_signal && (
        <SignalBadge label={`Reclaim: ${vwap.reclaim_strength}`} color={strengthColor(vwap.reclaim_strength)} />
      )}
    </Box>
  );
}

function ObvBlock({ obv }: { obv: Tech['obv'] | undefined }) {
  return (
    <Box sx={{ minWidth: 170, flex: 1 }}>
      <BlockTitle>On-Balance Volume</BlockTitle>
      {obv ? (
        <>
          <MetricRow label="OBV Trend" value={obv.obv_trend} color={signalColor(obv.obv_trend)} />
          <MetricRow label="Price Trend" value={obv.price_trend} color={signalColor(obv.price_trend)} />
          {obv.divergence !== 'none' && (
            <SignalBadge label={`${obv.divergence} div (${obv.divergence_strength})`}
              color={obv.divergence === 'bullish' ? GREEN : RED} />
          )}
        </>
      ) : <NoData />}
    </Box>
  );
}

function VolumeBlock({ volume }: { volume: Tech['volume_analysis'] | undefined }) {
  if (!volume) {
    return <Box sx={{ minWidth: 200, flex: 1.5 }}><BlockTitle>Volume Analysis</BlockTitle><NoData /></Box>;
  }
  return (
    <Box sx={{ minWidth: 200, flex: 1.5 }}>
      <BlockTitle>Volume Analysis</BlockTitle>
      <MetricRow label="Vol/Avg Ratio" value={`${volume.last_volume_ratio.toFixed(2)}×`}
        color={volume.last_volume_ratio >= 2 ? RED : 'var(--qc-text)'} />
      <MetricRow label="OBV Divergence" value={volume.obv_divergence ? 'Yes' : 'No'}
        color={volume.obv_divergence ? GREEN : 'var(--qc-faint)'} />
      <Box sx={{ mt: 0.5 }}>
        <Typography variant="caption" sx={{ color: 'var(--qc-muted)', fontSize: 11 }}>
          {volume.bottom_signal}
        </Typography>
      </Box>
      {volume.climax_events.slice(0, 2).map((e, i) => (
        <Box key={i} sx={{ mt: 0.5 }}>
          <Chip size="small" label={`${e.date} ${e.direction} vol ${e.volume_ratio}×`}
            sx={{ fontSize: 10, height: 18,
              bgcolor: e.direction === 'down' ? 'var(--qc-tint-green)' : 'var(--qc-tint-blue)',
              color: 'var(--qc-on-tint-green-soft)' }} />
        </Box>
      ))}
    </Box>
  );
}

// The four blocks and the two notes are split out so the panel itself only
// orders them: their optional reads put one unit at CC 12 (#441 review).
function MomentumBlocks({ tech }: { tech: Tech | undefined }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} flexWrap="wrap">
      <StochasticBlock stoch={tech?.stochastic} />
      <VwapBlock vwap={tech?.vwap} />
      <ObvBlock obv={tech?.obv} />
      <VolumeBlock volume={tech?.volume_analysis} />
    </Stack>
  );
}

function TechnicalNotes({ tech }: { tech: Tech | undefined }) {
  const vwapNote = tech?.vwap?.interpretation;
  const obvNote = tech?.obv?.interpretation;
  return (
    <>
      {vwapNote && <Note sx={{ mt: 1 }}>{vwapNote}</Note>}
      {obvNote && <Note>{obvNote}</Note>}
    </>
  );
}

export default function MomentumPanel({ ticker }: { ticker: string }) {
  const { data: tech, isLoading, error, refetch } = useTechnicalSignals(ticker, true);
  return (
    <Paper sx={{ p: 2 }}>
      <SectionHeader title="Momentum & Volume" />
      <SectionBody loading={isLoading} error={error} fallback="Failed to load technical signals" onRetry={refetch}>
        <MomentumBlocks tech={tech} />
      </SectionBody>
      <PartialErrors errors={tech?._errors} prefix="Some signals failed to load" onRetry={() => refetch()} sx={{ mt: 1 }} />
      <TechnicalNotes tech={tech} />
    </Paper>
  );
}
