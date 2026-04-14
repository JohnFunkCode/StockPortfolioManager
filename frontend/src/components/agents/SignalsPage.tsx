/**
 * SignalsPage — top-level page at /agents/signals.
 * Displays all agent signals fired in the last N days, with
 * direction and symbol filters.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import { useAgentSignals } from '../../hooks/useAgents';
import type { AgentSignal } from '../../api/agents';
import ErrorAlert from '../common/ErrorAlert';
import LoadingSpinner from '../common/LoadingSpinner';

// Score bar: filled segments out of 9
function ScoreBar({ score }: { score: number }) {
  const abs   = Math.abs(score);
  const color = score > 0 ? '#10b981' : score < 0 ? '#ef4444' : '#6b7280';
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Typography
        variant="body2"
        sx={{ width: 24, textAlign: 'right', fontWeight: 700, color, fontSize: 12 }}
      >
        {score > 0 ? `+${score}` : score}
      </Typography>
      <Stack direction="row" spacing={0.25}>
        {Array.from({ length: 9 }).map((_, i) => (
          <Box
            key={i}
            sx={{
              width: 5, height: 12, borderRadius: 0.5,
              bgcolor: i < abs ? color : 'rgba(255,255,255,0.08)',
            }}
          />
        ))}
      </Stack>
    </Stack>
  );
}

function DirectionChip({ direction }: { direction: AgentSignal['direction'] }) {
  const config = {
    buy:     { label: 'BUY',     color: '#10b981', bg: '#1e3a2f' },
    sell:    { label: 'SELL',    color: '#ef4444', bg: '#3a1e1e' },
    neutral: { label: 'NEUTRAL', color: '#9ca3af', bg: '#1f2937' },
  }[direction];
  return (
    <Chip
      size="small"
      label={config.label}
      sx={{ fontSize: 10, height: 18, bgcolor: config.bg, color: config.color, fontWeight: 700 }}
    />
  );
}

function TriggersCell({ triggers }: { triggers: Record<string, unknown> }) {
  const keys = Object.keys(triggers);
  if (keys.length === 0) return <Typography variant="caption" color="text.secondary">—</Typography>;
  return (
    <Stack direction="row" flexWrap="wrap" gap={0.5}>
      {keys.slice(0, 6).map((k) => (
        <Tooltip key={k} title={String(triggers[k])}>
          <Chip size="small" label={k} sx={{ fontSize: 9, height: 16, cursor: 'default' }} />
        </Tooltip>
      ))}
      {keys.length > 6 && (
        <Typography variant="caption" color="text.secondary">+{keys.length - 6}</Typography>
      )}
    </Stack>
  );
}

const DAYS_OPTIONS = [7, 14, 30, 60, 90];

export default function SignalsPage() {
  const navigate = useNavigate();
  const [symbol,    setSymbol]    = useState('');
  const [direction, setDirection] = useState<'buy' | 'sell' | 'neutral' | ''>('');
  const [days,      setDays]      = useState(30);

  const { data, isLoading, error } = useAgentSignals({
    symbol:    symbol.trim().toUpperCase() || undefined,
    direction: direction || undefined,
    days,
  });

  if (isLoading) return <LoadingSpinner />;
  if (error)     return <ErrorAlert message={(error as Error).message} />;

  const signals = data?.signals ?? [];

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 3 }}>
        <BoltIcon sx={{ color: 'primary.main' }} />
        <Typography variant="h4">Agent Signals</Typography>
        {data && (
          <Chip
            size="small"
            label={`${data.count} signal${data.count !== 1 ? 's' : ''}`}
            sx={{ ml: 1 }}
          />
        )}
      </Stack>

      {/* Filters */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={2} flexWrap="wrap" alignItems="center">
          <TextField
            label="Symbol"
            size="small"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            inputProps={{ maxLength: 10 }}
            sx={{ width: 120 }}
          />
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Direction</InputLabel>
            <Select
              value={direction}
              label="Direction"
              onChange={(e) => setDirection(e.target.value as typeof direction)}
            >
              <MenuItem value="">All</MenuItem>
              <MenuItem value="buy">Buy</MenuItem>
              <MenuItem value="sell">Sell</MenuItem>
              <MenuItem value="neutral">Neutral</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 110 }}>
            <InputLabel>Look-back</InputLabel>
            <Select
              value={days}
              label="Look-back"
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {DAYS_OPTIONS.map((d) => (
                <MenuItem key={d} value={d}>{d}d</MenuItem>
              ))}
            </Select>
          </FormControl>
        </Stack>
      </Paper>

      {signals.length === 0 ? (
        <Typography color="text.secondary" sx={{ mt: 2 }}>
          No signals found for the selected filters.
        </Typography>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell>Direction</TableCell>
                <TableCell>Score</TableCell>
                <TableCell>Triggers</TableCell>
                <TableCell>Escalated</TableCell>
                <TableCell>Fired At</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {signals.map((sig) => (
                <TableRow
                  key={sig.id}
                  hover
                  sx={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/securities/${sig.symbol}`)}
                >
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 700, color: 'primary.main' }}>
                      {sig.symbol}
                    </Typography>
                  </TableCell>
                  <TableCell><DirectionChip direction={sig.direction} /></TableCell>
                  <TableCell><ScoreBar score={sig.score} /></TableCell>
                  <TableCell><TriggersCell triggers={sig.triggers} /></TableCell>
                  <TableCell>
                    {sig.escalated ? (
                      <Chip size="small" label="P3" sx={{ fontSize: 10, height: 18, bgcolor: '#1e2a4a', color: '#60a5fa' }} />
                    ) : (
                      <Typography variant="caption" color="text.secondary">—</Typography>
                    )}
                  </TableCell>
                  <TableCell sx={{ color: '#9ca3af', fontSize: 12, whiteSpace: 'nowrap' }}>
                    {sig.fired_at ? new Date(sig.fired_at).toLocaleString() : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Box>
  );
}
