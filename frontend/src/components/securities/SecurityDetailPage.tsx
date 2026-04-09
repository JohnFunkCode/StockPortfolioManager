import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  Divider,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Skeleton,
  Stack,
  Tab,
  Tabs,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { useTechnicals, useOptionsLatest, useOptionsHistory } from '../../hooks/useSecurities';
import PriceChart from './charts/PriceChart';
import RSIChart from './charts/RSIChart';
import MACDChart from './charts/MACDChart';
import VolumeChart from './charts/VolumeChart';
import OptionsChainChart from './charts/OptionsChainChart';
import PCRatioChart from './charts/PCRatioChart';
import ErrorAlert from '../common/ErrorAlert';

const DAYS_OPTIONS = [30, 60, 90, 180, 365];

function ChartSkeleton({ height = 200 }: { height?: number }) {
  return <Skeleton variant="rectangular" width="100%" height={height} sx={{ borderRadius: 1 }} />;
}

export default function SecurityDetailPage() {
  const { symbol = '' } = useParams<{ symbol: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState(0);
  const [days, setDays] = useState(180);
  const [pcDays, setPcDays] = useState(30);

  const ticker = symbol.toUpperCase();

  const {
    data: techData,
    isLoading: techLoading,
    error: techError,
  } = useTechnicals(ticker, days);

  const {
    data: optLatest,
    isLoading: optLoading,
  } = useOptionsLatest(ticker);

  const {
    data: optHistory,
    isLoading: pcLoading,
  } = useOptionsHistory(ticker, pcDays);

  const indicators = techData?.indicators ?? [];
  const latest = indicators.at(-1);

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Button
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate('/securities')}
          size="small"
        >
          Back
        </Button>
        <Typography variant="h4" sx={{ fontWeight: 700 }}>
          {ticker}
        </Typography>
        {latest?.close != null && (
          <Typography variant="h5" color="text.secondary">
            ${latest.close.toFixed(2)}
          </Typography>
        )}
        {latest?.rsi != null && (
          <Chip
            size="small"
            label={`RSI ${latest.rsi.toFixed(1)}`}
            color={latest.rsi >= 70 ? 'error' : latest.rsi <= 30 ? 'success' : 'default'}
          />
        )}
      </Stack>

      {techError && <ErrorAlert message={(techError as Error).message} />}

      {/* Tabs */}
      <Paper sx={{ mb: 2 }}>
        <Tabs value={tab} onChange={(_e, v) => setTab(v)} variant="scrollable">
          <Tab label="Price & MAs" />
          <Tab label="Technical Analysis" />
          <Tab label="Options Chain" />
          <Tab label="Options Performance" />
        </Tabs>
      </Paper>

      {/* --- Tab 0: Price Chart --- */}
      {tab === 0 && (
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="subtitle1">Price with Moving Averages &amp; Bollinger Bands</Typography>
            <FormControl size="small" sx={{ minWidth: 100 }}>
              <InputLabel>Period</InputLabel>
              <Select
                value={days}
                label="Period"
                onChange={(e) => setDays(Number(e.target.value))}
              >
                {DAYS_OPTIONS.map((d) => (
                  <MenuItem key={d} value={d}>{d}d</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>

          {techLoading ? (
            <>
              <ChartSkeleton height={300} />
              <ChartSkeleton height={120} />
            </>
          ) : (
            <>
              <PriceChart
                data={indicators}
                showMAs={{ ma30: true, ma50: true, ma200: true }}
                showBB
                height={300}
              />
              <Divider sx={{ my: 1 }} />
              <VolumeChart data={indicators} height={100} />
            </>
          )}

          {/* Key levels summary */}
          {latest && (
            <Stack direction="row" spacing={3} sx={{ mt: 2, flexWrap: 'wrap' }}>
              {[
                { label: 'MA30', val: latest.ma30, color: '#f59e0b' },
                { label: 'MA50', val: latest.ma50, color: '#3b82f6' },
                { label: 'MA100', val: latest.ma100, color: '#8b5cf6' },
                { label: 'MA200', val: latest.ma200, color: '#ef4444' },
                { label: 'BB Upper', val: latest.bb_upper, color: '#6366f1' },
                { label: 'BB Lower', val: latest.bb_lower, color: '#6366f1' },
              ].map(({ label, val, color }) => (
                <Box key={label}>
                  <Typography variant="caption" sx={{ color: '#9ca3af' }}>{label}</Typography>
                  <Typography variant="body2" sx={{ color, fontWeight: 600 }}>
                    {val != null ? `$${val.toFixed(2)}` : '—'}
                    {val != null && latest.close != null && (
                      <span style={{ fontSize: 11, color: latest.close >= val ? '#10b981' : '#ef4444', marginLeft: 4 }}>
                        {latest.close >= val ? '▲' : '▼'}
                      </span>
                    )}
                  </Typography>
                </Box>
              ))}
            </Stack>
          )}
        </Paper>
      )}

      {/* --- Tab 1: Technical Analysis --- */}
      {tab === 1 && (
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle1">Technical Indicators</Typography>
              <FormControl size="small" sx={{ minWidth: 100 }}>
                <InputLabel>Period</InputLabel>
                <Select
                  value={days}
                  label="Period"
                  onChange={(e) => setDays(Number(e.target.value))}
                >
                  {DAYS_OPTIONS.map((d) => (
                    <MenuItem key={d} value={d}>{d}d</MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>

            {techLoading ? (
              <Stack spacing={1}>
                <ChartSkeleton height={140} />
                <ChartSkeleton height={150} />
                <ChartSkeleton height={100} />
              </Stack>
            ) : (
              <>
                <RSIChart data={indicators} height={140} />
                <Divider sx={{ my: 1 }} />
                <MACDChart data={indicators} height={150} />
                <Divider sx={{ my: 1 }} />
                <VolumeChart data={indicators} height={100} />
              </>
            )}
          </Paper>

          {/* RSI / MACD interpretation */}
          {latest && !techLoading && (
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle2" gutterBottom>Signal Summary</Typography>
              <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
                {latest.rsi != null && (
                  <Chip
                    label={`RSI ${latest.rsi.toFixed(1)} — ${
                      latest.rsi >= 70 ? 'Overbought' : latest.rsi <= 30 ? 'Oversold' : 'Neutral'
                    }`}
                    color={latest.rsi >= 70 ? 'error' : latest.rsi <= 30 ? 'success' : 'default'}
                    size="small"
                  />
                )}
                {latest.macd != null && latest.macd_signal != null && (
                  <Chip
                    label={`MACD ${latest.macd > latest.macd_signal ? 'Bullish' : 'Bearish'} crossover`}
                    color={latest.macd > latest.macd_signal ? 'success' : 'error'}
                    size="small"
                  />
                )}
                {latest.close != null && latest.ma50 != null && (
                  <Chip
                    label={`Price ${latest.close > latest.ma50 ? 'above' : 'below'} MA50`}
                    color={latest.close > latest.ma50 ? 'success' : 'error'}
                    size="small"
                  />
                )}
                {latest.close != null && latest.ma200 != null && (
                  <Chip
                    label={`Price ${latest.close > latest.ma200 ? 'above' : 'below'} MA200`}
                    color={latest.close > latest.ma200 ? 'success' : 'error'}
                    size="small"
                  />
                )}
              </Stack>
            </Paper>
          )}
        </Stack>
      )}

      {/* --- Tab 2: Options Chain --- */}
      {tab === 2 && (
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="subtitle1">Options Chain</Typography>
            {optLatest?.snapshot && (
              <Typography variant="caption" color="text.secondary">
                Snapshot: {optLatest.snapshot.captured_at.slice(0, 16).replace('T', ' ')} UTC
                &nbsp;·&nbsp;Price: ${optLatest.snapshot.price.toFixed(2)}
              </Typography>
            )}
          </Stack>

          {optLoading ? (
            <ChartSkeleton height={300} />
          ) : optLatest?.snapshot ? (
            <OptionsChainChart snapshot={optLatest.snapshot} />
          ) : (
            <Typography variant="body2" color="text.secondary">
              No options snapshot data found for {ticker}.
              Run the MCP server's <code>get_stock_price</code> tool to collect options data.
            </Typography>
          )}
        </Paper>
      )}

      {/* --- Tab 3: Options Performance --- */}
      {tab === 3 && (
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="subtitle1">Put/Call Ratio History</Typography>
            <FormControl size="small" sx={{ minWidth: 100 }}>
              <InputLabel>Period</InputLabel>
              <Select
                value={pcDays}
                label="Period"
                onChange={(e) => setPcDays(Number(e.target.value))}
              >
                {[7, 14, 30, 60, 90].map((d) => (
                  <MenuItem key={d} value={d}>{d}d</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>

          {pcLoading ? (
            <ChartSkeleton height={200} />
          ) : (optHistory?.history ?? []).length > 0 ? (
            <>
              <PCRatioChart data={optHistory!.history} height={220} />
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2" gutterBottom>Interpretation</Typography>
              <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
                <Typography variant="body2" color="text.secondary">
                  P/C &gt; 1.0 = more put buyers → bearish sentiment
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  P/C &lt; 0.7 = call dominated → bullish sentiment or complacency
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Extreme readings can be contrarian signals
                </Typography>
              </Stack>
            </>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No historical options data for {ticker} in the last {pcDays} days.
              Data is collected each time the MCP server's <code>get_stock_price</code> tool is called.
            </Typography>
          )}
        </Paper>
      )}

      {/* Global D3 tooltip div (shared across all charts) */}
      <div
        id="price-tooltip"
        style={{
          display: 'none',
          position: 'fixed',
          background: 'rgba(17,24,39,0.92)',
          color: '#f9fafb',
          padding: '6px 10px',
          borderRadius: 6,
          fontSize: 12,
          pointerEvents: 'none',
          zIndex: 9999,
          boxShadow: '0 2px 8px rgba(0,0,0,0.5)',
          lineHeight: 1.6,
        }}
      />
    </Box>
  );
}
