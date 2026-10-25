/**
 * Signals tab, section 3: Options Flow (Smart Money) — delta-adjusted OI and
 * unusual call sweeps from `/signals/options-flow`. Split out of SignalsTab
 * (#439 follow-up).
 */
import { Box, Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useOptionsFlowSignals } from '../../hooks/useSecurities';
import type { OptionsFlowResponse } from '../../api/securitiesTypes';
import SectionHeader from './SectionHeader';
import {
  AMBER, BlockTitle, GREEN, MetricRow, Note, PartialErrors, RED, SectionBody, SignalBadge,
  signalColor, signedFixed, strengthColor,
} from './signalsShared';

type Daoi = NonNullable<OptionsFlowResponse['delta_adjusted_oi']>;
type Unusual = NonNullable<OptionsFlowResponse['unusual_calls']>;
type Call = Unusual['unusual_calls'][number];

function DaoiBlock({ daoi }: { daoi: OptionsFlowResponse['delta_adjusted_oi'] | undefined }) {
  if (daoi?.net_daoi_shares == null) {
    return (
      <Typography variant="body2" color="text.secondary">
        Delta-adjusted OI not available (no options data).
      </Typography>
    );
  }
  const d: Daoi = daoi;
  const buy = d.mm_hedge_bias === 'buy_on_rally';
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
      <Box sx={{ flex: 1 }}>
        <BlockTitle>Delta-Adjusted OI (Market Maker)</BlockTitle>
        <MetricRow label="Net DAOI (shares)" value={daoi.net_daoi_shares.toLocaleString()}
          color={daoi.net_daoi_shares > 0 ? GREEN : RED} />
        <MetricRow label="MM Hedge Bias" value={buy ? 'Buy on rally' : 'Sell on rally'} color={buy ? GREEN : RED} />
        {d.gamma_wall_strike && <MetricRow label="Gamma Wall" value={`$${d.gamma_wall_strike}`} color={AMBER} />}
        {d.delta_flip_strike && <MetricRow label="Delta Flip" value={`$${d.delta_flip_strike}`} color="#6366f1" />}
        {d.dist_to_flip_pct != null && (
          <MetricRow label="Dist to Flip" value={`${signedFixed(d.dist_to_flip_pct)}%`} color="var(--qc-muted)" />
        )}
        <SignalBadge label={`Signal: ${d.signal}`} color={strengthColor(d.signal)} />
        <Note sx={{ mt: 0.5 }}>{d.mm_note}</Note>
      </Box>
    </Stack>
  );
}

const CONVICTION_BG: Record<string, string> = {
  'very high': 'var(--qc-tint-red-strong)',
  high: 'var(--qc-tint-amber-strong)',
};

function CallRow({ c }: { c: Call }) {
  return (
    <TableRow>
      <TableCell>{c.expiration}</TableCell>
      <TableCell align="right">${c.strike}</TableCell>
      <TableCell align="right">${c.last.toFixed(2)}</TableCell>
      <TableCell align="right">{c.iv.toFixed(1)}%</TableCell>
      <TableCell align="right">{c.volume.toLocaleString()}</TableCell>
      <TableCell align="right" sx={{ color: c.vol_oi_ratio >= 1 ? GREEN : 'var(--qc-text)' }}>
        {c.vol_oi_ratio.toFixed(2)}×
      </TableCell>
      <TableCell align="right" sx={{ color: c.otm_pct > 0 ? 'var(--qc-muted)' : GREEN }}>
        {signedFixed(c.otm_pct)}%
      </TableCell>
      <TableCell>
        <Chip size="small" label={c.conviction}
          sx={{ fontSize: 10, height: 18, bgcolor: CONVICTION_BG[c.conviction] ?? 'var(--qc-surface)', color: 'var(--qc-text)' }} />
      </TableCell>
    </TableRow>
  );
}

function CallsTable({ calls }: { calls: Call[] }) {
  return (
    <Box sx={{ overflowX: 'auto' }}>
      <Table size="small" sx={{ '& td, & th': { fontSize: 11, py: 0.4 } }}>
        <TableHead>
          <TableRow>
            <TableCell>Expiry</TableCell>
            <TableCell align="right">Strike</TableCell>
            <TableCell align="right">Last</TableCell>
            <TableCell align="right">IV</TableCell>
            <TableCell align="right">Vol</TableCell>
            <TableCell align="right">Vol/OI</TableCell>
            <TableCell align="right">OTM%</TableCell>
            <TableCell>Conviction</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {calls.slice(0, 8).map((c, i) => <CallRow key={i} c={c} />)}
        </TableBody>
      </Table>
    </Box>
  );
}

function UnusualCallsBlock({ unusual }: { unusual: OptionsFlowResponse['unusual_calls'] | undefined }) {
  if (!unusual) {
    return <Typography variant="body2" color="text.secondary">Unusual call data not available.</Typography>;
  }
  const u: Unusual = unusual;
  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <BlockTitle>Unusual Call Sweeps</BlockTitle>
        <SignalBadge label={`${u.sweep_signal} sweep signal`} color={signalColor(u.sweep_signal)} />
      </Stack>
      {u.unusual_calls.length > 0 ? (
        <CallsTable calls={u.unusual_calls} />
      ) : (
        <Typography variant="body2" sx={{ color: 'var(--qc-faint)', fontStyle: 'italic' }}>
          {u.interpretation}
        </Typography>
      )}
    </Box>
  );
}

export default function OptionsFlowPanel({ ticker }: { ticker: string }) {
  const { data: flow, isLoading, error, refetch } = useOptionsFlowSignals(ticker, true);
  return (
    <Paper sx={{ p: 2 }}>
      <SectionHeader title="Options Flow (Smart Money)" />
      <SectionBody loading={isLoading} error={error} fallback="Failed to load options flow signals" onRetry={refetch}>
        <Stack spacing={2}>
          <DaoiBlock daoi={flow?.delta_adjusted_oi} />
          <PartialErrors errors={flow?._errors} prefix="Some flow signals failed" onRetry={() => refetch()} />
          <UnusualCallsBlock unusual={flow?.unusual_calls} />
        </Stack>
      </SectionBody>
    </Paper>
  );
}
