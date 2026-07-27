/**
 * Structural-spread card for the chat sidekick — self-fetching from a ticker,
 * mirroring PriceChartCard's composition.
 *
 * Displays only what the API returns. Every number here is computed in
 * quantcore/analytics and quantcore/services/arbitrage.py (arch-v2 Rule 8);
 * this file formats, it never derives.
 *
 * The layout is built around one editorial decision: the NET discount is the
 * headline and the GROSS figure is shown beneath it, struck through and
 * labelled. Both numbers come back from the API, and showing the gross one
 * alone — or larger — would reproduce the exact misreading the scanner exists
 * to prevent.
 */
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { useArbitragePair } from '../../hooks/useArbitrage';
import type { ArbitrageFactors } from '../../api/arbitrage';

const VERDICT_COLOR: Record<string, 'success' | 'warning' | 'default'> = {
  candidate: 'success',
  watch: 'warning',
  reject: 'default',
};

/** Multiplicative factors, in the order the score applies them. */
const FACTOR_LABELS: Array<[keyof ArbitrageFactors, string, string]> = [
  ['evidence', 'Evidence', 'Cointegration strength and mean-reversion speed'],
  ['convergence', 'Convergence', 'How strongly something forces the gap closed'],
  ['hedge', 'Hedge', 'Halved when the only clean hedge is a futures contract'],
  ['carry', 'Carry', 'Fraction of the gap surviving the wait'],
  ['trend', 'Trend', 'Reduced when the spread is still widening'],
  ['freshness', 'Freshness', 'Reduced when curated holdings have gone stale'],
];

function pct(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : `${value.toFixed(digits)}%`;
}

export default function ArbitrageCard({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useArbitragePair(ticker);

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Analysing {ticker} against its underlying…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">Couldn&apos;t analyse {ticker}.</Alert>;
  }
  if (!data) return null;
  if (data.error) {
    return (
      <Alert severity="info">
        {data.error}
        {data.hint ? ` ${data.hint}` : ''}
      </Alert>
    );
  }

  const nav = data.nav;
  const navAvailable = Boolean(nav?.available);
  const factors = data.factors;

  return (
    <Box data-testid="arbitrage-card">
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="subtitle2">
          {data.security} vs {data.underlying}
        </Typography>
        <Chip size="small" label={data.kind.replace('_', ' ')} variant="outlined" />
        <Chip
          size="small"
          label={data.verdict}
          color={VERDICT_COLOR[data.verdict] ?? 'default'}
        />
        {data.score != null && (
          <Typography variant="body2" color="text.secondary">
            score {data.score}
          </Typography>
        )}
      </Stack>

      {/* Headline gap */}
      {navAvailable ? (
        <Box sx={{ mb: 1.5 }}>
          <Typography variant="h5" component="div" data-testid="headline-gap">
            {pct(nav?.premium_discount_pct)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            discount to net asset value — after debt and preferred ranking ahead
            of the common
          </Typography>
          {nav?.gross_premium_discount_pct != null && (
            <Typography variant="caption" display="block" sx={{ color: 'text.disabled' }}>
              headline gap to gross assets{' '}
              <Box component="span" sx={{ textDecoration: 'line-through' }}>
                {pct(nav.gross_premium_discount_pct)}
              </Box>{' '}
              — overstates what a shareholder gets
            </Typography>
          )}
        </Box>
      ) : (
        <Box sx={{ mb: 1.5 }}>
          <Typography variant="h5" component="div" data-testid="headline-gap">
            {data.statistics.zscore.z == null
              ? '—'
              : `${data.statistics.zscore.z.toFixed(2)}σ`}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            spread vs its own history
            {nav?.reason ? ' — no curated holdings, so no NAV math' : ''}
          </Typography>
        </Box>
      )}

      {/* Supporting numbers */}
      <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
        {navAvailable && nav?.carry_drag_pct != null && (
          <Metric label="Carry" value={`${nav.carry_drag_pct.toFixed(2)}%/yr`} />
        )}
        {navAvailable && nav?.exposure_ratio != null && (
          <Metric label="Exposure" value={`${nav.exposure_ratio.toFixed(2)}×`} />
        )}
        <Metric
          label="Half-life"
          value={
            data.statistics.half_life.half_life_days == null
              ? 'none'
              : `${data.statistics.half_life.half_life_days.toFixed(1)}d`
          }
        />
        <Metric
          label="Cointegrated"
          value={data.statistics.cointegration.cointegrated ? 'yes' : 'no'}
        />
        <Metric
          label="Hedge"
          value={data.hedge.available ? (data.hedge.instrument ?? '—') : 'futures only'}
        />
      </Stack>

      {/* Score decomposition */}
      {factors && (
        <>
          <Divider sx={{ my: 1 }} />
          <Typography variant="caption" color="text.secondary">
            Score factors (multiplied)
          </Typography>
          <Stack spacing={0.5} sx={{ mt: 0.5 }}>
            {factors.opportunity != null && (
              <FactorRow
                label="Opportunity"
                hint="Gap size, scaled 0–100"
                value={factors.opportunity}
                display={factors.opportunity.toFixed(1)}
                max={100}
              />
            )}
            {FACTOR_LABELS.map(([key, label, hint]) => {
              const value = factors[key];
              if (value == null) return null;
              return (
                <FactorRow
                  key={key}
                  label={label}
                  hint={hint}
                  value={value}
                  display={`×${value}`}
                  max={1}
                />
              );
            })}
          </Stack>
        </>
      )}

      {/* What breaks it */}
      {data.breaks_on.length > 0 && (
        <>
          <Divider sx={{ my: 1 }} />
          <Typography variant="caption" color="text.secondary">
            What breaks it
          </Typography>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
            {data.breaks_on.map((risk) => (
              <Chip key={risk} size="small" label={risk} color="warning" variant="outlined" />
            ))}
          </Stack>
        </>
      )}

      {nav?.holdings_stale && (
        <Alert severity="warning" sx={{ mt: 1, py: 0 }}>
          Holdings figures are {nav.holdings_age_days} days old.
        </Alert>
      )}
    </Box>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

function FactorRow({
  label,
  hint,
  value,
  display,
  max,
}: {
  label: string;
  hint: string;
  value: number;
  display: string;
  max: number;
}) {
  return (
    <Tooltip title={hint} placement="left">
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="caption" sx={{ minWidth: 88 }}>
          {label}
        </Typography>
        <LinearProgress
          variant="determinate"
          value={Math.min(100, (value / max) * 100)}
          sx={{ flex: 1, height: 6, borderRadius: 3 }}
        />
        <Typography variant="caption" sx={{ minWidth: 44, textAlign: 'right' }}>
          {display}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
