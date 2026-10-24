import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Chip, CircularProgress, Collapse, Paper, Stack, Typography } from '@mui/material';
import { useScreener } from '../../hooks/useSecurities';
import type { ScreenerResult } from '../../api/securitiesTypes';
import { SENTIMENT_COLORS } from './sentiment';

const SCREENER_PRESETS = [
  { label: 'Oversold (RSI < 30)',    params: { rsi_max: '30' } },
  { label: 'Overbought (RSI > 70)',  params: { rsi_min: '70' } },
  { label: 'Near BB Lower',          params: { near_bb_lower: '1' } },
  { label: 'Near BB Upper',          params: { near_bb_upper: '1' } },
  { label: 'Above MA200',            params: { above_ma200: '1' } },
  { label: 'Below MA200',            params: { below_ma200: '1' } },
  { label: 'MACD Bullish',           params: { macd_bullish: '1' } },
  { label: 'MACD Bearish',           params: { macd_bearish: '1' } },
  { label: 'News: Positive',         params: { news_sentiment: 'positive' } },
  { label: 'News: Negative',         params: { news_sentiment: 'negative' } },
] as const;

/** Technical screener presets over cached OHLCV; a result chip opens the detail page. */
export default function ScreenerPanel({ open }: { open: boolean }) {
  const navigate = useNavigate();
  const [params, setParams] = useState<Record<string, string>>({});
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const { data, isLoading } = useScreener(params, open && Object.keys(params).length > 0);

  const clear = () => { setActivePreset(null); setParams({}); };

  return (
    <Collapse in={open}>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Technical Screener (from cached OHLCV data)</Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {SCREENER_PRESETS.map((preset) => (
            <Chip
              key={preset.label}
              label={preset.label}
              clickable
              variant={activePreset === preset.label ? 'filled' : 'outlined'}
              color={activePreset === preset.label ? 'primary' : 'default'}
              onClick={() => {
                if (activePreset === preset.label) {
                  clear();
                } else {
                  setActivePreset(preset.label);
                  setParams(preset.params as Record<string, string>);
                }
              }}
              sx={{ fontSize: 12 }}
            />
          ))}
          {activePreset && (
            <Chip label="Clear" variant="outlined" clickable onClick={clear}
              sx={{ fontSize: 12, borderColor: '#ef4444', color: '#ef4444' }} />
          )}
        </Stack>

        {isLoading && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 2 }}>
            <CircularProgress size={18} />
            <Typography variant="body2" color="text.secondary">Scanning...</Typography>
          </Box>
        )}

        {data && !isLoading && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2" sx={{ mb: 1, color: 'var(--qc-muted)' }}>
              {data.count} match{data.count !== 1 ? 'es' : ''} — click to navigate
            </Typography>
            <Stack direction="row" flexWrap="wrap" spacing={1} useFlexGap>
              {data.results.map((r: ScreenerResult) => {
                const sentColor = r.news_sentiment
                  ? SENTIMENT_COLORS[r.news_sentiment]?.bg
                  : undefined;
                const bg = sentColor
                  ?? (r.rsi != null && r.rsi <= 30 ? 'var(--qc-tint-green)'
                    : r.rsi != null && r.rsi >= 70 ? 'var(--qc-tint-red)'
                    : 'var(--qc-surface)');
                return (
                  <Chip
                    key={r.symbol}
                    label={
                      `${r.symbol}` +
                      (r.news_sentiment ? ` · ${r.news_sentiment}` : '') +
                      (r.rsi != null ? ` · RSI ${r.rsi.toFixed(0)}` : '') +
                      (r.last_close != null ? ` · $${r.last_close.toFixed(2)}` : '')
                    }
                    clickable
                    onClick={() => navigate(`/securities/${r.symbol}`)}
                    sx={{ fontSize: 12, bgcolor: bg, color: 'var(--qc-text)' }}
                  />
                );
              })}
            </Stack>
          </Box>
        )}
      </Paper>
    </Collapse>
  );
}
