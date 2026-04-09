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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { useTechnicals, useOptionsLatest, useOptionsHistory, useOptionsAnalytics, useIVRank, useEarnings } from '../../hooks/useSecurities';
import SignalsTab from './SignalsTab';
import PriceChart from './charts/PriceChart';
import RSIChart from './charts/RSIChart';
import MACDChart from './charts/MACDChart';
import VolumeChart from './charts/VolumeChart';
import OptionsChainChart from './charts/OptionsChainChart';
import PCRatioChart from './charts/PCRatioChart';
import MaxPainChart from './charts/MaxPainChart';
import ErrorAlert from '../common/ErrorAlert';

function daysToExpiry(expiration: string): number {
  return Math.round((new Date(expiration).getTime() - Date.now()) / 86_400_000);
}

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
  const [analyticsExpIdx, setAnalyticsExpIdx] = useState(0);

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

  const {
    data: analyticsData,
    isLoading: analyticsLoading,
  } = useOptionsAnalytics(ticker);

  const { data: ivRankData } = useIVRank(ticker);
  const { data: earningsData } = useEarnings(ticker);

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
          <Tab label="Options Analytics" />
          <Tab label="Signals" />
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
                earningsDates={earningsData?.earnings_dates}
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

      {/* --- Tab 4: Options Analytics (Max Pain + Expected Move) --- */}
      {tab === 4 && (() => {
        const rows = analyticsData?.analytics ?? [];
        const selectedRow = rows[analyticsExpIdx] ?? rows[0];
        const price = analyticsData?.price ?? 0;

        return (
          <Stack spacing={2}>
            {/* IV Rank / IV Percentile panel */}
            {ivRankData && ivRankData.current_iv != null && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="subtitle1" gutterBottom>IV Rank &amp; Percentile</Typography>
                <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap">
                  {/* Metric boxes */}
                  {[
                    { label: 'Current IV', value: `${ivRankData.current_iv.toFixed(1)}%`, color: '#f9fafb' },
                    {
                      label: 'IV Rank',
                      value: ivRankData.iv_rank != null ? `${ivRankData.iv_rank.toFixed(0)}` : '—',
                      color: ivRankData.iv_rank != null
                        ? ivRankData.iv_rank >= 80 ? '#ef4444'
                        : ivRankData.iv_rank >= 50 ? '#f59e0b'
                        : '#10b981'
                        : '#9ca3af',
                      suffix: ivRankData.iv_rank != null ? '' : '',
                    },
                    {
                      label: 'IV Percentile',
                      value: ivRankData.iv_percentile != null ? `${ivRankData.iv_percentile.toFixed(0)}` : '—',
                      color: ivRankData.iv_percentile != null
                        ? ivRankData.iv_percentile >= 80 ? '#ef4444'
                        : ivRankData.iv_percentile >= 50 ? '#f59e0b'
                        : '#10b981'
                        : '#9ca3af',
                    },
                    { label: '52w High IV', value: ivRankData.iv_52w_high != null ? `${ivRankData.iv_52w_high.toFixed(1)}%` : '—', color: '#ef4444' },
                    { label: '52w Low IV',  value: ivRankData.iv_52w_low  != null ? `${ivRankData.iv_52w_low.toFixed(1)}%`  : '—', color: '#10b981' },
                    { label: 'Data Points', value: `${ivRankData.data_points}`, color: '#9ca3af' },
                  ].map(({ label, value, color }) => (
                    <Box key={label}>
                      <Typography variant="caption" sx={{ color: '#6b7280' }}>{label}</Typography>
                      <Typography variant="body1" sx={{ color, fontWeight: 700, fontSize: 18 }}>{value}</Typography>
                    </Box>
                  ))}
                  {/* Range bar */}
                  {ivRankData.iv_rank != null && ivRankData.iv_52w_high != null && ivRankData.iv_52w_low != null && (
                    <Box sx={{ flex: 1, minWidth: 180 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280' }}>52-Week IV Range</Typography>
                      <Box sx={{ position: 'relative', height: 8, bgcolor: '#1f2937', borderRadius: 4, mt: 0.5 }}>
                        {/* gradient fill from low to current */}
                        <Box sx={{
                          position: 'absolute', left: 0, top: 0, bottom: 0,
                          width: `${ivRankData.iv_rank}%`,
                          bgcolor: ivRankData.iv_rank >= 80 ? '#ef4444' : ivRankData.iv_rank >= 50 ? '#f59e0b' : '#10b981',
                          borderRadius: 4,
                          transition: 'width 0.4s',
                        }} />
                        {/* current position marker */}
                        <Box sx={{
                          position: 'absolute', top: -2, bottom: -2,
                          left: `calc(${ivRankData.iv_rank}% - 2px)`,
                          width: 4, bgcolor: '#f9fafb', borderRadius: 2,
                        }} />
                      </Box>
                      <Stack direction="row" justifyContent="space-between">
                        <Typography variant="caption" sx={{ color: '#10b981', fontSize: 9 }}>Low</Typography>
                        <Typography variant="caption" sx={{ color: '#ef4444', fontSize: 9 }}>High</Typography>
                      </Stack>
                    </Box>
                  )}
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                  IV Rank = where current IV sits in the 52-week range (0 = low, 100 = high).
                  IV Percentile = % of past snapshots with IV below today.
                  Composite IV = average of avg call IV + avg put IV across all stored expirations.
                </Typography>
              </Paper>
            )}

            {/* Expected Move table */}
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle1" gutterBottom>
                Expected Move &amp; Max Pain — All Expirations
              </Typography>
              {analyticsLoading ? (
                <ChartSkeleton height={160} />
              ) : rows.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No full chain data for {ticker}. Run <code>get_full_options_chain</code> via MCP first.
                </Typography>
              ) : (
                <Box sx={{ overflowX: 'auto' }}>
                  <Table size="small" sx={{ '& td, & th': { fontSize: 12, py: 0.5 } }}>
                    <TableHead>
                      <TableRow>
                        <TableCell>Expiration</TableCell>
                        <TableCell align="right">DTE</TableCell>
                        <TableCell align="right">ATM Strike</TableCell>
                        <TableCell align="right">EM ±$</TableCell>
                        <TableCell align="right">EM %</TableCell>
                        <TableCell align="right">Upper</TableCell>
                        <TableCell align="right">Lower</TableCell>
                        <TableCell align="right">Max Pain</TableCell>
                        <TableCell align="right">P/C Ratio</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {rows.map((row, i) => {
                        const dte = daysToExpiry(row.expiration);
                        const isSelected = i === analyticsExpIdx;
                        return (
                          <TableRow
                            key={row.expiration}
                            hover
                            selected={isSelected}
                            onClick={() => setAnalyticsExpIdx(i)}
                            sx={{ cursor: 'pointer' }}
                          >
                            <TableCell sx={{ fontWeight: isSelected ? 700 : 400 }}>
                              {row.expiration}
                            </TableCell>
                            <TableCell align="right" sx={{ color: dte <= 7 ? '#ef4444' : dte <= 21 ? '#f59e0b' : '#9ca3af' }}>
                              {dte}d
                            </TableCell>
                            <TableCell align="right">${row.atm_strike?.toFixed(2) ?? '—'}</TableCell>
                            <TableCell align="right" sx={{ color: '#f59e0b', fontWeight: 600 }}>
                              ±${row.expected_move_dollar.toFixed(2)}
                            </TableCell>
                            <TableCell align="right">
                              <Chip
                                size="small"
                                label={`${row.expected_move_pct.toFixed(1)}%`}
                                sx={{
                                  fontSize: 10, height: 18,
                                  bgcolor: row.expected_move_pct > 10 ? '#7f1d1d'
                                    : row.expected_move_pct > 5 ? '#92400e'
                                    : '#1f2937',
                                  color: '#f9fafb',
                                }}
                              />
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#10b981' }}>
                              ${row.upper_bound.toFixed(2)}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#ef4444' }}>
                              ${row.lower_bound.toFixed(2)}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#f59e0b', fontWeight: 600 }}>
                              {row.max_pain != null ? `$${row.max_pain}` : '—'}
                            </TableCell>
                            <TableCell align="right">
                              {row.put_call_ratio != null
                                ? <Chip
                                    size="small"
                                    label={row.put_call_ratio.toFixed(2)}
                                    sx={{
                                      fontSize: 10, height: 18,
                                      bgcolor: row.put_call_ratio > 1 ? '#1e3a2f' : '#1e2a3a',
                                      color: row.put_call_ratio > 1 ? '#10b981' : '#60a5fa',
                                    }}
                                  />
                                : '—'}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </Box>
              )}
            </Paper>

            {/* Max Pain chart for selected expiration */}
            {selectedRow && selectedRow.pain_curve.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                  <Typography variant="subtitle1">
                    Max Pain — {selectedRow.expiration}
                    {' '}
                    <Typography component="span" variant="caption" color="text.secondary">
                      (click a row above to change expiration)
                    </Typography>
                  </Typography>
                  <Stack direction="row" spacing={2}>
                    <Box>
                      <Typography variant="caption" sx={{ color: '#9ca3af' }}>Max Pain</Typography>
                      <Typography variant="body2" sx={{ color: '#f59e0b', fontWeight: 700 }}>
                        ${selectedRow.max_pain ?? '—'}
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" sx={{ color: '#9ca3af' }}>Current Price</Typography>
                      <Typography variant="body2" sx={{ color: '#10b981', fontWeight: 700 }}>
                        ${price.toFixed(2)}
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" sx={{ color: '#9ca3af' }}>Distance</Typography>
                      <Typography variant="body2" sx={{ fontWeight: 700,
                        color: selectedRow.max_pain != null && Math.abs(price - selectedRow.max_pain) / price < 0.02
                          ? '#f59e0b' : '#9ca3af' }}>
                        {selectedRow.max_pain != null
                          ? `${((selectedRow.max_pain - price) / price * 100).toFixed(1)}%`
                          : '—'}
                      </Typography>
                    </Box>
                  </Stack>
                </Stack>
                <MaxPainChart
                  painCurve={selectedRow.pain_curve}
                  currentPrice={price}
                  maxPainStrike={selectedRow.max_pain}
                  height={300}
                />
                <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                  Bar height = total dollar value (OI × 100) expiring worthless if settled at that strike.
                  Max pain (amber) is where options sellers lose the least.
                </Typography>
              </Paper>
            )}
          </Stack>
        );
      })()}

      {/* --- Tab 5: Signals --- */}
      {tab === 5 && <SignalsTab ticker={ticker} />}

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
