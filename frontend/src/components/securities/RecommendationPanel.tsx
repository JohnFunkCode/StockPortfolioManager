/**
 * RecommendationPanel — "Agent Analysis" tab content for SecurityDetailPage.
 * Shows the most recent deep analysis recommendation for the current symbol.
 */
import {
  Alert,
  Box,
  Chip,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import RemoveIcon from '@mui/icons-material/Remove';
import BlockIcon from '@mui/icons-material/Block';
import { useAgentRecommendations } from '../../hooks/useAgents';
import type { AgentRecommendation } from '../../api/agents';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props { ticker: string }

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const REC_CONFIG: Record<AgentRecommendation['recommendation'], {
  label: string; color: string; bg: string; icon: React.ReactNode
}> = {
  BUY:   { label: 'BUY',   color: '#10b981', bg: '#1e3a2f', icon: <TrendingUpIcon />   },
  SELL:  { label: 'SELL',  color: '#ef4444', bg: '#3a1e1e', icon: <TrendingDownIcon /> },
  HOLD:  { label: 'HOLD',  color: '#f59e0b', bg: '#3a2a0e', icon: <RemoveIcon />       },
  AVOID: { label: 'AVOID', color: '#9ca3af', bg: '#1f2937', icon: <BlockIcon />        },
};

const CONV_CONFIG: Record<AgentRecommendation['conviction'], { color: string }> = {
  HIGH:   { color: '#10b981' },
  MEDIUM: { color: '#f59e0b' },
  LOW:    { color: '#9ca3af' },
};

function fmt(v: number | null, prefix = '$') {
  return v != null ? `${prefix}${v.toFixed(2)}` : '—';
}

function ScoreGauge({ score }: { score: number }) {
  const abs   = Math.abs(score);
  const max   = 27;
  const pct   = Math.min(abs / max, 1);
  const color = score > 0 ? '#10b981' : score < 0 ? '#ef4444' : '#6b7280';

  return (
    <Box sx={{ width: 160 }}>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary">Score</Typography>
        <Typography variant="caption" sx={{ fontWeight: 700, color }}>
          {score > 0 ? `+${score}` : score} / 27
        </Typography>
      </Stack>
      <Box sx={{ height: 6, borderRadius: 3, bgcolor: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
        <Box sx={{ height: '100%', width: `${pct * 100}%`, bgcolor: color, borderRadius: 3, transition: 'width 0.4s' }} />
      </Box>
    </Box>
  );
}

function BullBearCase({ label, items, color }: { label: string; items: string[]; color: string }) {
  if (!items || items.length === 0) return null;
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ color, mb: 0.75, fontWeight: 700 }}>{label}</Typography>
      <Stack spacing={0.5}>
        {items.map((pt, i) => (
          <Stack key={i} direction="row" spacing={1} alignItems="flex-start">
            <Typography sx={{ color, fontSize: 12, lineHeight: 1.8, flexShrink: 0 }}>•</Typography>
            <Typography variant="body2" sx={{ fontSize: 12, color: '#d1d5db', lineHeight: 1.6 }}>{pt}</Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function RecommendationPanel({ ticker }: Props) {
  const { data, isLoading } = useAgentRecommendations(ticker, 1);

  if (isLoading) return <LoadingSpinner />;

  const rec = data?.recommendations?.[0] ?? null;

  if (!rec) {
    return (
      <Alert severity="info" sx={{ mt: 2 }}>
        No deep analysis has been run for <strong>{ticker}</strong> yet. Deep Analysis is triggered
        automatically when the Signal Scanner fires a conviction-level signal, or you can trigger it
        manually via <code>POST /run-deep-analysis</code>.
      </Alert>
    );
  }

  const recCfg  = REC_CONFIG[rec.recommendation];
  const convCfg = CONV_CONFIG[rec.conviction];
  const score   = rec.details?.score as number | undefined;
  const bull    = rec.details?.bull_case as string[] | undefined;
  const bear    = rec.details?.bear_case as string[] | undefined;
  const play    = rec.details?.options_play as string | undefined;

  return (
    <Box>
      {/* Header */}
      <Paper sx={{ p: 2.5, mb: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={3} alignItems={{ sm: 'center' }}>
          {/* Recommendation badge */}
          <Box sx={{
            display: 'flex', alignItems: 'center', gap: 1.5,
            px: 2.5, py: 1.5, borderRadius: 2,
            bgcolor: recCfg.bg,
            border: `1px solid ${recCfg.color}33`,
          }}>
            <Box sx={{ color: recCfg.color, display: 'flex' }}>{recCfg.icon}</Box>
            <Box>
              <Typography sx={{ fontSize: 22, fontWeight: 900, color: recCfg.color, lineHeight: 1 }}>
                {recCfg.label}
              </Typography>
              <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 0.25 }}>
                <Typography variant="caption" color="text.secondary">Conviction</Typography>
                <Typography variant="caption" sx={{ fontWeight: 700, color: convCfg.color }}>
                  {rec.conviction}
                </Typography>
              </Stack>
            </Box>
          </Box>

          {/* Score gauge */}
          {score != null && <ScoreGauge score={score} />}

          {/* Price levels */}
          <Stack spacing={0.4} sx={{ ml: { sm: 'auto' } }}>
            <Stack direction="row" spacing={2}>
              <Box>
                <Typography variant="caption" color="text.secondary">Entry</Typography>
                <Typography variant="body2" sx={{ fontWeight: 600, fontSize: 13 }}>
                  {rec.entry_low != null && rec.entry_high != null
                    ? `${fmt(rec.entry_low)} – ${fmt(rec.entry_high)}`
                    : fmt(rec.entry_low ?? rec.entry_high)}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">Target</Typography>
                <Typography variant="body2" sx={{ fontWeight: 600, color: '#10b981', fontSize: 13 }}>
                  {fmt(rec.price_target)}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">Stop</Typography>
                <Typography variant="body2" sx={{ fontWeight: 600, color: '#ef4444', fontSize: 13 }}>
                  {fmt(rec.stop_loss)}
                </Typography>
              </Box>
            </Stack>
          </Stack>
        </Stack>

        {/* Timestamp */}
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
          Analysis run {rec.fired_at ? new Date(rec.fired_at).toLocaleString() : '—'}
        </Typography>
      </Paper>

      {/* Bull / Bear case */}
      {(bull?.length || bear?.length) ? (
        <Paper sx={{ p: 2.5, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ mb: 2, fontWeight: 700 }}>Thesis</Typography>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={3}>
            <Box sx={{ flex: 1 }}>
              <BullBearCase label="Bull Case" items={bull ?? []} color="#10b981" />
            </Box>
            {bull?.length && bear?.length ? (
              <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
            ) : null}
            <Box sx={{ flex: 1 }}>
              <BullBearCase label="Bear Case" items={bear ?? []} color="#ef4444" />
            </Box>
          </Stack>
        </Paper>
      ) : null}

      {/* Options play */}
      {play && (
        <Paper sx={{ p: 2.5 }}>
          <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 700 }}>Options Play</Typography>
          <Typography variant="body2" sx={{ color: '#d1d5db', lineHeight: 1.7, fontStyle: 'italic' }}>
            {play}
          </Typography>
        </Paper>
      )}

      {/* Conviction chip legend */}
      <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
        {(['HIGH', 'MEDIUM', 'LOW'] as const).map((c) => (
          <Chip
            key={c}
            size="small"
            label={c}
            sx={{
              fontSize: 10, height: 18,
              bgcolor: c === rec.conviction ? CONV_CONFIG[c].color + '22' : 'transparent',
              color:   CONV_CONFIG[c].color,
              border:  c === rec.conviction ? `1px solid ${CONV_CONFIG[c].color}` : '1px solid transparent',
            }}
          />
        ))}
        <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center', ml: 0.5 }}>
          conviction
        </Typography>
      </Stack>
    </Box>
  );
}
