/**
 * StopLossCard — the stop-loss synthesis for one ticker (#399): technical,
 * trailing and confluence stops side by side, a ladder replaying the worst
 * historical drops from today's price against them, the context behind them,
 * and P&L at each stop when the caller holds the symbol.
 *
 * Self-fetching from just a ticker, so the Sidekick can render it too. Every
 * number comes from /stop-loss (quantcore/analytics/stops.py); the ladder only
 * maps the given prices to positions on a track.
 */
import { Alert, Box, Button, Chip, CircularProgress, Paper, Stack, Tooltip, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { scaleLinear } from 'd3';
import { useStopLoss } from '../../hooks/useSecurities';
import { NO_SUPPORT_BELOW_PRICE } from '../../api/securitiesTypes';
import type {
  DrawdownReplayRow,
  StopLossConfluence,
  StopLossPosition,
  StopLossResponse,
} from '../../api/securitiesTypes';

interface Props {
  ticker: string;
  /** 'full' on the Technical Analysis tab, which also feeds the confluence table. */
  confluence?: Exclude<StopLossConfluence, 'none'>;
}

const TECHNICAL = '#f59e0b';
const TRAILING = '#6366f1';
const CONFLUENCE = '#10b981';

const money = (v: number) => `$${v.toFixed(2)}`;
const signedPct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
const signedMoney = (v: number) => `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`;

function SectionHeader({ title }: { title: string }) {
  return (
    <Typography variant="subtitle2" sx={{ color: 'var(--qc-muted)', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
      {title}
    </Typography>
  );
}

function Caption({ children }: { children: React.ReactNode }) {
  return <Typography variant="caption" sx={{ color: 'var(--qc-faint)', display: 'block' }}>{children}</Typography>;
}

function StopTile({ testId, title, tag, color, price, detail, warning, children }: {
  testId: string;
  title: string;
  tag?: string;
  color: string;
  price: string;
  detail: string;
  warning?: string;
  children?: React.ReactNode;
}) {
  return (
    <Box data-testid={testId}
      sx={{ flex: 1, minWidth: 180, p: 1.5, borderRadius: 1, border: '1px solid var(--qc-line)', borderTop: `3px solid ${color}`, bgcolor: 'var(--qc-surface-sunk)' }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="caption" sx={{ color: 'var(--qc-muted)', fontWeight: 600 }}>{title}</Typography>
        {tag && <Typography variant="caption" sx={{ color: 'var(--qc-faint)', fontStyle: 'italic' }}>{tag}</Typography>}
      </Stack>
      <Typography sx={{ color, fontWeight: 700, fontSize: 20 }}>{price}</Typography>
      <Caption>{detail}</Caption>
      {children}
      {warning && (
        <Typography variant="caption" sx={{ color: '#f97316', display: 'block', mt: 0.5 }}>{warning}</Typography>
      )}
    </Box>
  );
}

function ConfluenceTile({ data }: { data: StopLossResponse }) {
  const stop = data.stops.confluence_stop;
  const reason = data.stops.confluence_stop_reason;
  if (!stop) {
    return (
      <StopTile testId="stop-tile-confluence" title="Confluence" tag="proposed" color={CONFLUENCE}
        price="—" detail={reason ?? 'No confluence stop available.'} />
    );
  }
  return (
    <StopTile testId="stop-tile-confluence" title="Confluence" tag="proposed" color={CONFLUENCE}
      price={money(stop.stop)} detail={`${signedPct(stop.distance_pct)} · under ${money(stop.zone_low)}–${money(stop.zone_high)}`}>
      <Caption>{stop.method_count} methods agree · score {stop.score.toFixed(2)}</Caption>
      {stop.stepped_past_noise && reason && (
        <Typography variant="caption" sx={{ color: 'var(--qc-muted)', display: 'block', mt: 0.5 }}>{reason}</Typography>
      )}
    </StopTile>
  );
}

function technicalAnchor(data: StopLossResponse): string {
  const t = data.technical;
  if (t.primary_support === NO_SUPPORT_BELOW_PRICE) return 'no support below price';
  return `under ${t.primary_support.replace(/_/g, ' ')} ${money(t.primary_support_price)}`;
}

function StopTiles({ data }: { data: StopLossResponse }) {
  const s = data.stops;
  return (
    <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap data-testid="stop-tiles">
      <StopTile testId="stop-tile-technical" title="Technical" color={TECHNICAL}
        price={money(s.technical_stop)}
        detail={`${signedPct(s.technical_stop_distance_pct)} · ${technicalAnchor(data)}`}
        warning={s.technical_stop_inside_noise_floor
          ? `Inside the noise floor: closer than the worst 1-day drop (${data.drawdown.max_1day_pct.toFixed(1)}%).`
          : undefined} />
      <StopTile testId="stop-tile-trailing" title="Trailing" tag="if placed today" color={TRAILING}
        price={money(s.trailing_stop_price)}
        detail={`${s.trailing_stop_pct.toFixed(1)}% trail (base ${data.drawdown.base_trailing_stop_pct.toFixed(1)}%)`} />
      {'confluence_stop' in s && <ConfluenceTile data={data} />}
    </Stack>
  );
}

interface LadderStop {
  label: string; color: string; price: number;
  hitKey: 'hits_technical' | 'hits_trailing' | 'hits_confluence';
}

function ladderStops(data: StopLossResponse): LadderStop[] {
  const out: LadderStop[] = [
    { label: 'Technical', color: TECHNICAL, price: data.stops.technical_stop, hitKey: 'hits_technical' },
    { label: 'Trailing', color: TRAILING, price: data.stops.trailing_stop_price, hitKey: 'hits_trailing' },
  ];
  if (data.stops.confluence_stop) {
    out.push({ label: 'Confluence', color: CONFLUENCE, price: data.stops.confluence_stop.stop, hitKey: 'hits_confluence' });
  }
  return out;
}

/** Which stops a replayed drop would trigger, in words (the server's hits_* verdicts). */
function ReplayVerdict({ row, stops }: { row: DrawdownReplayRow; stops: LadderStop[] }) {
  const hit = stops.filter((s) => row[s.hitKey] === true);
  return (
    <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap
      data-testid={`replay-verdict-${row.key}`}>
      {hit.length === 0 ? (
        <Caption>Clears every stop</Caption>
      ) : (
        <>
          <Typography variant="caption" sx={{ color: 'var(--qc-text-soft)' }}>Triggers</Typography>
          {hit.map((s) => (
            <Stack key={s.label} direction="row" spacing={0.4} alignItems="center">
              <Box sx={{ width: 8, height: 8, borderRadius: 0.25, bgcolor: s.color }} />
              <Typography variant="caption" sx={{ color: 'var(--qc-text-soft)', fontWeight: 600 }}>{s.label}</Typography>
            </Stack>
          ))}
        </>
      )}
    </Stack>
  );
}

// The label column and the track share these bases, so the axis row wraps exactly
// as the replay rows do: side by side on the page, stacked in the Sidekick rail.
const LABEL_COL = { flex: '0 0 150px' } as const;
const TRACK_COL = { flex: '1 1 160px' } as const;

/** Round-dollar ticks under the tracks, with today's price at the right end. */
function LadderAxis({ ticks, hi, at }: { ticks: number[]; hi: number; at: (v: number) => number }) {
  return (
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
      <Box sx={LABEL_COL} />
      <Box data-testid="stop-ladder-axis" sx={{ ...TRACK_COL, position: 'relative', height: 18, borderTop: '1px solid var(--qc-line)' }}>
        {ticks.map((v) => (
          <Box key={v} sx={{ position: 'absolute', left: `${at(v)}%`, top: 0, transform: 'translateX(-50%)', textAlign: 'center' }}>
            <Box sx={{ width: '1px', height: 4, bgcolor: 'var(--qc-faint)', mx: 'auto' }} />
            <Typography variant="caption" sx={{ color: 'var(--qc-faint)', fontSize: 10, lineHeight: 1 }}>${v}</Typography>
          </Box>
        ))}
        <Box sx={{ position: 'absolute', right: 0, top: 0, textAlign: 'right' }}>
          <Box sx={{ width: '1px', height: 4, bgcolor: 'var(--qc-text-soft)', ml: 'auto' }} />
          <Typography variant="caption" sx={{ color: 'var(--qc-text-soft)', fontSize: 10, lineHeight: 1, fontWeight: 600 }}>
            {money(hi)} now
          </Typography>
        </Box>
      </Box>
    </Stack>
  );
}

/**
 * Replay rows as bars falling from today's price, with each stop as a line, on a
 * shared price scale. A bar that reaches a stop line would have triggered that stop.
 */
function PriceLadder({ data }: { data: StopLossResponse }) {
  const stopsOnLadder = ladderStops(data);
  const prices = [...data.drawdown_replay.map((r) => r.price), ...stopsOnLadder.map((s) => s.price)];
  const hi = data.price;
  const low = Math.min(hi, ...prices);
  const lo = low - (hi - low) * 0.05;
  const at = (v: number) => (hi === lo ? 100 : ((v - lo) / (hi - lo)) * 100);
  // Round-dollar ticks; the right end is left for the price label.
  const ticks = scaleLinear().domain([lo, hi]).ticks(4).filter((v) => at(v) >= 0 && at(v) <= 85);

  return (
    <Box data-testid="stop-ladder">
      <Caption>Worst historical drops replayed from today’s {money(data.price)}. A bar that reaches a stop line would have triggered that stop.</Caption>
      <Stack spacing={1} sx={{ mt: 1 }}>
        {data.drawdown_replay.map((row) => (
          <Stack key={row.key} direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap
            data-testid={`replay-${row.key}`}>
            <Box sx={LABEL_COL}>
              <Typography variant="caption" sx={{ color: 'var(--qc-text-soft)', display: 'block', lineHeight: 1.2 }}>{row.label}</Typography>
              <Typography variant="caption" sx={{ color: 'var(--qc-faint)', display: 'block' }}>
                {row.drop_pct.toFixed(1)}% → {money(row.price)}
              </Typography>
              <ReplayVerdict row={row} stops={stopsOnLadder} />
            </Box>
            <Box sx={{ ...TRACK_COL, position: 'relative', height: 16, bgcolor: 'var(--qc-surface)', borderRadius: 0.5 }}>
              {ticks.map((v) => (
                <Box key={v} sx={{ position: 'absolute', top: 0, bottom: 0, width: '1px', left: `${at(v)}%`, bgcolor: 'var(--qc-line)' }} />
              ))}
              <Box sx={{
                position: 'absolute', top: 3, bottom: 3, left: `${at(row.price)}%`, right: 0,
                bgcolor: '#ef444455', borderLeft: '2px solid #ef4444',
              }} />
              {stopsOnLadder.map((s) => (
                <Tooltip key={s.label} title={`${s.label} stop ${money(s.price)}`}>
                  <Box sx={{ position: 'absolute', top: -2, bottom: -2, width: 2, left: `${at(s.price)}%`, bgcolor: s.color }} />
                </Tooltip>
              ))}
            </Box>
          </Stack>
        ))}
        <LadderAxis ticks={ticks} hi={hi} at={at} />
      </Stack>
      <Stack direction="row" spacing={2} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        {stopsOnLadder.map((s) => (
          <Stack key={s.label} direction="row" spacing={0.5} alignItems="center">
            <Box sx={{ width: 10, height: 2, bgcolor: s.color }} />
            <Caption>{s.label} stop {money(s.price)}</Caption>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

function ContextRow({ label, value, color = 'var(--qc-text-soft)' }: { label: string; value: string; color?: string }) {
  return (
    <Stack direction="row" justifyContent="space-between" spacing={2}>
      <Typography variant="caption" sx={{ color: 'var(--qc-faint)' }}>{label}</Typography>
      <Typography variant="caption" sx={{ color, fontWeight: 600 }}>{value}</Typography>
    </Stack>
  );
}

function StopContext({ data }: { data: StopLossResponse }) {
  const t = data.technical;
  const si = data.short_interest;
  return (
    <Box data-testid="stop-context" sx={{ flex: 1, minWidth: 200 }}>
      <Caption>Context</Caption>
      <ContextRow label="VWAP" value={`${money(t.vwap)} · ${t.vwap_direction} ${t.consecutive_bars} bars`}
        color={t.above_vwap ? '#10b981' : '#ef4444'} />
      <ContextRow label="RSI / MACD" value={`${t.rsi.toFixed(1)} · ${t.macd_crossover}`} />
      {t.gamma_wall != null && <ContextRow label="Gamma wall" value={money(t.gamma_wall)} />}
      <ContextRow label="Short interest"
        value={si.data_available
          ? `${si.short_float_pct.toFixed(1)}% float · ${si.short_ratio_days.toFixed(1)}d · squeeze ${si.squeeze_potential.toLowerCase()}`
          : 'unavailable'} />
    </Box>
  );
}

function isHeld(data: StopLossResponse): boolean {
  return data.position_source != null && 'cost_basis' in data.position;
}

function PositionPnl({ position }: { position: StopLossPosition }) {
  const pnlColor = (v: number) => (v >= 0 ? '#10b981' : '#ef4444');
  const unit = position.shares > 0 ? '' : ' /sh';
  return (
    <Box data-testid="stop-position" sx={{ flex: 1, minWidth: 200 }}>
      <Caption>
        Position · {position.shares > 0 ? `${position.shares} sh @ ` : 'cost '}{money(position.cost_basis)}
      </Caption>
      <ContextRow label="Unrealized"
        value={`${signedMoney(position.total_unrealized_pnl)}${unit} (${signedPct(position.unrealized_pnl_pct)})`}
        color={pnlColor(position.total_unrealized_pnl)} />
      <ContextRow label="At technical stop" value={`${signedMoney(position.pnl_at_technical_stop)}${unit}`}
        color={pnlColor(position.pnl_at_technical_stop)} />
      <ContextRow label="At trailing stop" value={`${signedMoney(position.pnl_at_trailing_stop)}${unit}`}
        color={pnlColor(position.pnl_at_trailing_stop)} />
      {position.pnl_at_confluence_stop != null && (
        <ContextRow label="At confluence stop" value={`${signedMoney(position.pnl_at_confluence_stop)}${unit}`}
          color={pnlColor(position.pnl_at_confluence_stop)} />
      )}
    </Box>
  );
}

function StopLossBody({ data }: { data: StopLossResponse }) {
  return (
    <Stack spacing={2}>
      <StopTiles data={data} />
      <PriceLadder data={data} />
      <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap data-testid="stop-details">
        <StopContext data={data} />
        {isHeld(data) && <PositionPnl position={data.position as StopLossPosition} />}
      </Stack>
      {data.flags.length > 0 && (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap data-testid="stop-flags">
          {data.flags.map((f) => (
            <Chip key={f} size="small" label={f.replace(/_/g, ' ')} variant="outlined" sx={{ fontSize: 10, height: 20 }} />
          ))}
        </Stack>
      )}
      <Typography variant="caption" sx={{ color: 'var(--qc-muted)', display: 'block', fontStyle: 'italic', lineHeight: 1.5 }}>
        {data.summary}
      </Typography>
    </Stack>
  );
}

export default function StopLossCard({ ticker, confluence = 'stop' }: Props) {
  const { data, isLoading, isFetching, error, refetch } = useStopLoss(ticker, confluence);

  return (
    <Paper sx={{ p: 2 }} data-testid="stop-loss-card">
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap">
        <SectionHeader title="Stop-Loss" />
        {data && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="caption" sx={{ color: 'var(--qc-faint)' }}>Price {money(data.price)}</Typography>
            {isHeld(data) && (
              <Chip size="small" label="Held" data-testid="stop-held-chip"
                sx={{ fontSize: 11, height: 20, fontWeight: 600, bgcolor: 'var(--qc-tint-green)', color: 'var(--qc-ink-green)', border: '1px solid var(--qc-ink-green)' }} />
            )}
          </Stack>
        )}
      </Stack>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
      ) : error ? (
        <Alert severity="error" sx={{ fontSize: 12 }}
          action={
            <Button size="small" color="inherit" disabled={isFetching}
              startIcon={isFetching ? <CircularProgress size={14} color="inherit" /> : <RefreshIcon />}
              onClick={() => refetch()}>
              {isFetching ? 'Retrying…' : 'Retry'}
            </Button>
          }>
          {(error as Error).message ?? 'Failed to load the stop-loss analysis'}
        </Alert>
      ) : data ? (
        <StopLossBody data={data} />
      ) : null}
    </Paper>
  );
}
