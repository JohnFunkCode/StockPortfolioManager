/**
 * SignalsTab — Signals tab content for SecurityDetailPage.
 * Shows technical momentum, structure, options flow, and risk signals.
 */
import { Alert, Box, Button, Chip, CircularProgress, Divider, Link, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTechnicalSignals, useOptionsFlowSignals, useRiskSignals, useNews } from '../../hooks/useSecurities';
import type { NewsResponse } from '../../api/securitiesTypes';

interface Props { ticker: string }

function SignalBadge({ label, color }: { label: string; color: string }) {
  return (
    <Chip size="small" label={label}
      sx={{ fontSize: 11, height: 20, bgcolor: color + '22', color, borderColor: color, border: '1px solid' }} />
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <Typography variant="subtitle2" sx={{ color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
      {title}
    </Typography>
  );
}

function MetricRow({ label, value, color = '#f9fafb' }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ py: 0.4 }}>
      <Typography variant="body2" sx={{ color: '#6b7280', fontSize: 12 }}>{label}</Typography>
      <Typography variant="body2" sx={{ color, fontWeight: 600, fontSize: 12 }}>{value}</Typography>
    </Stack>
  );
}

function SectionError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert
      severity="error"
      action={
        <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={onRetry}>
          Retry
        </Button>
      }
      sx={{ fontSize: 12 }}
    >
      {message}
    </Alert>
  );
}

function strengthColor(s: string | undefined): string {
  if (!s) return '#6b7280';
  if (s === 'strong')   return '#10b981';
  if (s === 'moderate') return '#f59e0b';
  if (s === 'weak')     return '#f97316';
  if (s === 'none')     return '#6b7280';
  return '#9ca3af';
}

function signalColor(s: string | undefined): string {
  if (!s) return '#6b7280';
  const sl = s.toLowerCase();
  if (sl.includes('bull') || sl.includes('strong') || sl.includes('above') || sl.includes('rising') || sl.includes('buy')) return '#10b981';
  if (sl.includes('bear') || sl.includes('below') || sl.includes('sell') || sl.includes('falling') || sl.includes('overbought')) return '#ef4444';
  if (sl.includes('neutral') || sl.includes('none') || sl.includes('flat')) return '#6b7280';
  return '#f59e0b';
}

function buildSignalsSummary(
  tech: import('../../api/securitiesTypes').TechnicalSignalsResponse | undefined,
  flow: import('../../api/securitiesTypes').OptionsFlowResponse | undefined,
  risk: import('../../api/securitiesTypes').RiskSignalsResponse | undefined,
  news: NewsResponse | undefined,
): string[] {
  if (!tech && !flow && !risk && !news) return [];
  const lines: string[] = [];

  // --- Overall technical bias ---
  const bullishSignals: string[] = [];
  const bearishSignals: string[] = [];

  if (tech?.stochastic) {
    const { k, signal } = tech.stochastic;
    if (k <= 20 || signal.toLowerCase().includes('bull') || signal.toLowerCase().includes('oversold')) bullishSignals.push('stochastic oversold');
    else if (k >= 80 || signal.toLowerCase().includes('bear') || signal.toLowerCase().includes('overbought')) bearishSignals.push('stochastic overbought');
  }
  if (tech?.vwap) {
    if (tech.vwap.position === 'above_vwap') bullishSignals.push('price above VWAP');
    else bearishSignals.push('price below VWAP');
    if (tech.vwap.reclaim_signal) bullishSignals.push(`VWAP reclaim (${tech.vwap.reclaim_strength})`);
  }
  if (tech?.obv) {
    if (tech.obv.divergence === 'bullish') bullishSignals.push(`bullish OBV divergence (${tech.obv.divergence_strength})`);
    else if (tech.obv.divergence === 'bearish') bearishSignals.push(`bearish OBV divergence (${tech.obv.divergence_strength})`);
    if (tech.obv.obv_trend === 'rising' && tech.obv.price_trend === 'rising') bullishSignals.push('OBV and price trending up');
    else if (tech.obv.obv_trend === 'falling' && tech.obv.price_trend === 'falling') bearishSignals.push('OBV and price trending down');
  }
  if (tech?.higher_lows?.higher_low_pattern) {
    bullishSignals.push(`${tech.higher_lows.pattern_strength} higher-low structure (${tech.higher_lows.consecutive_higher_lows} consecutive)`);
  }
  if (tech?.candlestick_patterns && tech.candlestick_patterns.pattern_count > 0) {
    const bullPat = tech.candlestick_patterns.patterns_found.filter((p) => p.bias === 'bullish').length;
    const bearPat = tech.candlestick_patterns.patterns_found.filter((p) => p.bias === 'bearish').length;
    if (bullPat > bearPat) bullishSignals.push(`${bullPat} bullish candlestick pattern${bullPat > 1 ? 's' : ''}`);
    else if (bearPat > bullPat) bearishSignals.push(`${bearPat} bearish candlestick pattern${bearPat > 1 ? 's' : ''}`);
  }

  const net = bullishSignals.length - bearishSignals.length;
  if (bullishSignals.length > 0 || bearishSignals.length > 0) {
    if (net >= 2) {
      lines.push(`Technical picture is broadly bullish (${bullishSignals.length} bullish vs ${bearishSignals.length} bearish signal${bearishSignals.length !== 1 ? 's' : ''}): ${bullishSignals.join(', ')}.`);
    } else if (net <= -2) {
      lines.push(`Technical picture is broadly bearish (${bearishSignals.length} bearish vs ${bullishSignals.length} bullish signal${bullishSignals.length !== 1 ? 's' : ''}): ${bearishSignals.join(', ')}.`);
    } else {
      lines.push(`Technical signals are mixed — ${bullishSignals.length > 0 ? `bullish: ${bullishSignals.join(', ')}` : 'no clear bullish signals'}${bearishSignals.length > 0 ? `; bearish: ${bearishSignals.join(', ')}` : ''}.`);
    }
  }

  // --- Volume context ---
  if (tech?.volume_analysis) {
    const ratio = tech.volume_analysis.last_volume_ratio;
    if (ratio >= 2) {
      lines.push(`Volume is running ${ratio.toFixed(1)}× the average — unusually high activity that often precedes or confirms a directional move.`);
    } else if (ratio < 0.5) {
      lines.push(`Volume is thin at ${ratio.toFixed(1)}× average — low participation reduces confidence in any price move seen recently.`);
    }
    if (tech.volume_analysis.climax_events.length > 0) {
      const latest = tech.volume_analysis.climax_events[0];
      lines.push(`A ${latest.direction}-volume climax event (${latest.volume_ratio}× avg) was recorded on ${latest.date} — these extremes often mark short-term exhaustion points.`);
    }
  }

  // --- Gap structure ---
  if (tech?.gap_analysis && tech.gap_analysis.unfilled_count > 0) {
    const gaps = tech.gap_analysis.bounce_targets;
    if (gaps.length > 0) {
      const gapStr = gaps.slice(0, 2).map((g) => `$${g.gap_bottom.toFixed(2)}–$${g.gap_top.toFixed(2)} (${g.distance_pct > 0 ? '+' : ''}${g.distance_pct.toFixed(1)}%)`).join(', ');
      lines.push(`${tech.gap_analysis.unfilled_count} unfilled gap${tech.gap_analysis.unfilled_count > 1 ? 's' : ''} remain as potential reversion targets: ${gapStr}.`);
    }
  }

  // --- Options flow / smart money ---
  if (flow?.delta_adjusted_oi) {
    const daoi = flow.delta_adjusted_oi;
    const biasLabel = daoi.mm_hedge_bias === 'buy_on_rally' ? 'buy on rallies' : 'sell on rallies';
    lines.push(`Delta-adjusted OI shows ${daoi.net_daoi_shares.toLocaleString()} net shares of market-maker hedge exposure — MMs are positioned to ${biasLabel}${daoi.gamma_wall_strike ? `, with a gamma wall at $${daoi.gamma_wall_strike}` : ''}${daoi.delta_flip_strike ? ` and a delta flip point at $${daoi.delta_flip_strike}` : ''}.`);
  }
  if (flow?.unusual_calls) {
    const sweep = flow.unusual_calls.sweep_signal;
    const count = flow.unusual_calls.unusual_calls.length;
    if (count > 0) {
      const topCall = flow.unusual_calls.unusual_calls[0];
      lines.push(`Unusual call activity detected (${count} sweep${count > 1 ? 's' : ''}, ${sweep} signal): the largest is the $${topCall.strike} strike expiring ${topCall.expiration} with ${topCall.volume.toLocaleString()} contracts at ${topCall.vol_oi_ratio.toFixed(1)}× vol/OI — ${topCall.conviction} conviction.`);
    }
  }

  // --- Risk / stop context ---
  if (risk?.drawdown) {
    const stop = risk.drawdown.trailing_stop_pct;
    const worst = risk.drawdown.max_1day_drawdown_pct;
    lines.push(`Historical drawdown analysis suggests a minimum trailing stop of ${stop.toFixed(1)}% to avoid noise-driven exits (worst single-day drop: ${worst.toFixed(1)}%).`);
  }

  // --- News sentiment ---
  if (news?.sentiment_summary && news.sentiment_summary.scored_count > 0) {
    const { overall, positive_count, negative_count, neutral_count, scored_count } = news.sentiment_summary;
    const overallLabel = overall === 'positive' ? 'broadly positive' : overall === 'negative' ? 'broadly negative' : 'mixed/neutral';
    lines.push(
      `News sentiment across ${scored_count} recent article${scored_count !== 1 ? 's' : ''} is ${overallLabel} ` +
      `(${positive_count} positive, ${negative_count} negative, ${neutral_count} neutral) — ` +
      `${overall === 'negative' ? 'negative headlines may act as a near-term headwind' : overall === 'positive' ? 'positive news flow supports bullish momentum' : 'no strong directional bias from news'}.`
    );
  }

  return lines;
}

export default function SignalsTab({ ticker }: Props) {
  const { data: tech, isLoading: techLoading, error: techError, refetch: refetchTech } = useTechnicalSignals(ticker, true);
  const { data: flow, isLoading: flowLoading, error: flowError, refetch: refetchFlow } = useOptionsFlowSignals(ticker, true);
  const { data: risk, isLoading: riskLoading, error: riskError, refetch: refetchRisk } = useRiskSignals(ticker, true);
  const { data: news, isLoading: newsLoading, refetch: refetchNews } = useNews(ticker);

  const summaryLines = buildSignalsSummary(tech, flow, risk, news);
  const allLoading = techLoading || flowLoading || riskLoading;

  return (
    <Stack spacing={2}>

      {/* ── Interpretation summary ──────────────────────────────────── */}
      {!allLoading && summaryLines.length > 0 && (
        <Paper variant="outlined" sx={{ p: 2, borderColor: 'divider', bgcolor: 'background.default' }}>
          <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>
            Interpretation
          </Typography>
          <Stack spacing={0.75}>
            {summaryLines.map((line, i) => (
              <Typography key={i} variant="body2" sx={{ color: 'text.primary', lineHeight: 1.6 }}>
                {line}
              </Typography>
            ))}
          </Stack>
        </Paper>
      )}

      {/* ── Section 1: Momentum ─────────────────────────────────────── */}
      <Paper sx={{ p: 2 }}>
        <SectionHeader title="Momentum & Volume" />
        {techLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
        ) : techError ? (
          <SectionError message={(techError as Error).message ?? 'Failed to load technical signals'} onRetry={refetchTech} />
        ) : (
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} flexWrap="wrap">

            {/* Stochastic */}
            <Box sx={{ minWidth: 170, flex: 1 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>Stochastic Oscillator</Typography>
              {tech?.stochastic ? (
                <>
                  <MetricRow label="%K" value={tech.stochastic.k.toFixed(1)} color={tech.stochastic.k >= 80 ? '#ef4444' : tech.stochastic.k <= 20 ? '#10b981' : '#f9fafb'} />
                  <MetricRow label="%D" value={tech.stochastic.d.toFixed(1)} color="#9ca3af" />
                  <SignalBadge label={tech.stochastic.signal} color={signalColor(tech.stochastic.signal)} />
                </>
              ) : <Typography variant="body2" color="text.secondary">—</Typography>}
            </Box>

            {/* VWAP */}
            <Box sx={{ minWidth: 170, flex: 1 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>VWAP (20-day)</Typography>
              {tech?.vwap ? (
                <>
                  <MetricRow label="VWAP" value={`$${tech.vwap.vwap.toFixed(2)}`} />
                  <MetricRow label="Position" value={tech.vwap.position === 'above_vwap' ? 'Above' : 'Below'}
                    color={tech.vwap.position === 'above_vwap' ? '#10b981' : '#ef4444'} />
                  <MetricRow label="Distance" value={`${tech.vwap.distance_pct > 0 ? '+' : ''}${tech.vwap.distance_pct.toFixed(1)}%`}
                    color={Math.abs(tech.vwap.distance_pct) > 5 ? '#f59e0b' : '#9ca3af'} />
                  {tech.vwap.reclaim_signal && (
                    <SignalBadge label={`Reclaim: ${tech.vwap.reclaim_strength}`} color={strengthColor(tech.vwap.reclaim_strength)} />
                  )}
                </>
              ) : <Typography variant="body2" color="text.secondary">—</Typography>}
            </Box>

            {/* OBV */}
            <Box sx={{ minWidth: 170, flex: 1 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>On-Balance Volume</Typography>
              {tech?.obv ? (
                <>
                  <MetricRow label="OBV Trend" value={tech.obv.obv_trend} color={signalColor(tech.obv.obv_trend)} />
                  <MetricRow label="Price Trend" value={tech.obv.price_trend} color={signalColor(tech.obv.price_trend)} />
                  {tech.obv.divergence !== 'none' && (
                    <SignalBadge label={`${tech.obv.divergence} div (${tech.obv.divergence_strength})`}
                      color={tech.obv.divergence === 'bullish' ? '#10b981' : '#ef4444'} />
                  )}
                </>
              ) : <Typography variant="body2" color="text.secondary">—</Typography>}
            </Box>

            {/* Volume Analysis */}
            <Box sx={{ minWidth: 200, flex: 1.5 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>Volume Analysis</Typography>
              {tech?.volume_analysis ? (
                <>
                  <MetricRow label="Vol/Avg Ratio" value={`${tech.volume_analysis.last_volume_ratio.toFixed(2)}×`}
                    color={tech.volume_analysis.last_volume_ratio >= 2 ? '#ef4444' : '#f9fafb'} />
                  <MetricRow label="OBV Divergence" value={tech.volume_analysis.obv_divergence ? 'Yes' : 'No'}
                    color={tech.volume_analysis.obv_divergence ? '#10b981' : '#6b7280'} />
                  <Box sx={{ mt: 0.5 }}>
                    <Typography variant="caption" sx={{ color: '#9ca3af', fontSize: 11 }}>
                      {tech.volume_analysis.bottom_signal}
                    </Typography>
                  </Box>
                  {tech.volume_analysis.climax_events.slice(0, 2).map((e, i) => (
                    <Box key={i} sx={{ mt: 0.5 }}>
                      <Chip size="small" label={`${e.date} ${e.direction} vol ${e.volume_ratio}×`}
                        sx={{ fontSize: 10, height: 18,
                          bgcolor: e.direction === 'down' ? '#1e3a2f' : '#1e2a4a', color: '#d1fae5' }} />
                    </Box>
                  ))}
                </>
              ) : <Typography variant="body2" color="text.secondary">—</Typography>}
            </Box>
          </Stack>
        )}
        {tech?._errors && Object.keys(tech._errors).length > 0 && (
          <Alert severity="warning" sx={{ mt: 1, fontSize: 11 }}>
            Some signals failed to load: {Object.keys(tech._errors).join(', ')}
            <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={refetchTech} sx={{ ml: 1 }}>
              Retry
            </Button>
          </Alert>
        )}
        {tech?.vwap?.interpretation && (
          <Typography variant="caption" sx={{ color: '#9ca3af', mt: 1, display: 'block', fontStyle: 'italic' }}>
            {tech.vwap.interpretation}
          </Typography>
        )}
        {tech?.obv?.interpretation && (
          <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', fontStyle: 'italic' }}>
            {tech.obv.interpretation}
          </Typography>
        )}
      </Paper>

      {/* ── Section 2: Price Structure ──────────────────────────────── */}
      <Paper sx={{ p: 2 }}>
        <SectionHeader title="Price Structure" />
        {techLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
        ) : techError ? (
          <SectionError message={(techError as Error).message ?? 'Failed to load technical signals'} onRetry={refetchTech} />
        ) : (
          <Stack spacing={2}>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>

              {/* Higher Lows */}
              <Box sx={{ flex: 1 }}>
                <Typography variant="caption" sx={{ color: '#6b7280' }}>Higher Lows (Daily)</Typography>
                {tech?.higher_lows ? (
                  <>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
                      <SignalBadge
                        label={tech.higher_lows.higher_low_pattern
                          ? `${tech.higher_lows.pattern_strength} — ${tech.higher_lows.consecutive_higher_lows} consecutive`
                          : 'No pattern'}
                        color={tech.higher_lows.higher_low_pattern
                          ? strengthColor(tech.higher_lows.pattern_strength)
                          : '#6b7280'} />
                    </Stack>
                    <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', mt: 0.5, fontStyle: 'italic' }}>
                      {tech.higher_lows.interpretation}
                    </Typography>
                  </>
                ) : <Typography variant="body2" color="text.secondary">—</Typography>}
              </Box>

              {/* Gap Analysis */}
              <Box sx={{ flex: 1 }}>
                <Typography variant="caption" sx={{ color: '#6b7280' }}>Gap Analysis (60d)</Typography>
                {tech?.gap_analysis ? (
                  <>
                    <MetricRow label="Unfilled gaps" value={tech.gap_analysis.unfilled_count} />
                    <MetricRow label="Partial fills" value={tech.gap_analysis.partial_count} />
                    {tech.gap_analysis.bounce_targets.length > 0 && (
                      <Box sx={{ mt: 0.5 }}>
                        {tech.gap_analysis.bounce_targets.map((t, i) => (
                          <Typography key={i} variant="caption" sx={{ color: '#f59e0b', display: 'block', fontSize: 11 }}>
                            {t.direction === 'gap_up' ? '↑' : '↓'} ${t.gap_bottom.toFixed(2)}–${t.gap_top.toFixed(2)}
                            {' '}({t.distance_pct > 0 ? '+' : ''}{t.distance_pct.toFixed(1)}%)
                          </Typography>
                        ))}
                      </Box>
                    )}
                    <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', mt: 0.5, fontStyle: 'italic' }}>
                      {tech.gap_analysis.interpretation}
                    </Typography>
                  </>
                ) : <Typography variant="body2" color="text.secondary">—</Typography>}
              </Box>
            </Stack>

            {/* Candlestick Patterns */}
            {tech?.candlestick_patterns && tech.candlestick_patterns.pattern_count > 0 && (
              <Box>
                <Typography variant="caption" sx={{ color: '#6b7280' }}>
                  Candlestick Patterns (last 10 bars) — {tech.candlestick_patterns.bounce_signal}
                </Typography>
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
                      {tech.candlestick_patterns.patterns_found.slice(0, 5).map((p, i) => (
                        <TableRow key={i}>
                          <TableCell>{p.date}</TableCell>
                          <TableCell sx={{ fontFamily: 'monospace' }}>{p.pattern}</TableCell>
                          <TableCell>
                            <Chip size="small" label={p.bias}
                              sx={{ fontSize: 10, height: 18,
                                bgcolor: p.bias === 'bullish' ? '#1e3a2f' : p.bias === 'bearish' ? '#3a1e1e' : '#1e2937',
                                color: p.bias === 'bullish' ? '#10b981' : p.bias === 'bearish' ? '#ef4444' : '#9ca3af' }} />
                          </TableCell>
                          <TableCell>
                            <SignalBadge label={p.strength} color={strengthColor(p.strength)} />
                          </TableCell>
                          <TableCell sx={{ color: '#9ca3af', maxWidth: 200 }}>{p.notes.join('; ')}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              </Box>
            )}
          </Stack>
        )}
      </Paper>

      {/* ── Section 3: Options Flow ──────────────────────────────────── */}
      <Paper sx={{ p: 2 }}>
        <SectionHeader title="Options Flow (Smart Money)" />
        {flowLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
        ) : flowError ? (
          <SectionError message={(flowError as Error).message ?? 'Failed to load options flow signals'} onRetry={refetchFlow} />
        ) : (
          <Stack spacing={2}>
            {/* DAOI summary */}
            {flow?.delta_adjusted_oi ? (
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="caption" sx={{ color: '#6b7280' }}>Delta-Adjusted OI (Market Maker)</Typography>
                  <MetricRow label="Net DAOI (shares)" value={flow.delta_adjusted_oi.net_daoi_shares.toLocaleString()}
                    color={flow.delta_adjusted_oi.net_daoi_shares > 0 ? '#10b981' : '#ef4444'} />
                  <MetricRow label="MM Hedge Bias" value={flow.delta_adjusted_oi.mm_hedge_bias === 'buy_on_rally' ? 'Buy on rally' : 'Sell on rally'}
                    color={flow.delta_adjusted_oi.mm_hedge_bias === 'buy_on_rally' ? '#10b981' : '#ef4444'} />
                  {flow.delta_adjusted_oi.gamma_wall_strike && (
                    <MetricRow label="Gamma Wall" value={`$${flow.delta_adjusted_oi.gamma_wall_strike}`} color="#f59e0b" />
                  )}
                  {flow.delta_adjusted_oi.delta_flip_strike && (
                    <MetricRow label="Delta Flip" value={`$${flow.delta_adjusted_oi.delta_flip_strike}`} color="#6366f1" />
                  )}
                  {flow.delta_adjusted_oi.dist_to_flip_pct != null && (
                    <MetricRow label="Dist to Flip" value={`${flow.delta_adjusted_oi.dist_to_flip_pct > 0 ? '+' : ''}${flow.delta_adjusted_oi.dist_to_flip_pct.toFixed(1)}%`} color="#9ca3af" />
                  )}
                  <SignalBadge label={`Signal: ${flow.delta_adjusted_oi.signal}`} color={strengthColor(flow.delta_adjusted_oi.signal)} />
                  <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', mt: 0.5, fontStyle: 'italic' }}>
                    {flow.delta_adjusted_oi.mm_note}
                  </Typography>
                </Box>
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">Delta-adjusted OI not available (no options data).</Typography>
            )}

            {flow?._errors && Object.keys(flow._errors).length > 0 && (
              <Alert severity="warning" sx={{ fontSize: 11 }}>
                Some flow signals failed: {Object.keys(flow._errors).join(', ')}
                <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={refetchFlow} sx={{ ml: 1 }}>
                  Retry
                </Button>
              </Alert>
            )}

            {/* Unusual calls */}
            {flow?.unusual_calls ? (
              <Box>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                  <Typography variant="caption" sx={{ color: '#6b7280' }}>Unusual Call Sweeps</Typography>
                  <SignalBadge
                    label={`${flow.unusual_calls.sweep_signal} sweep signal`}
                    color={signalColor(flow.unusual_calls.sweep_signal)} />
                </Stack>
                {flow.unusual_calls.unusual_calls.length > 0 ? (
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
                        {flow.unusual_calls.unusual_calls.slice(0, 8).map((c, i) => (
                          <TableRow key={i}>
                            <TableCell>{c.expiration}</TableCell>
                            <TableCell align="right">${c.strike}</TableCell>
                            <TableCell align="right">${c.last.toFixed(2)}</TableCell>
                            <TableCell align="right">{c.iv.toFixed(1)}%</TableCell>
                            <TableCell align="right">{c.volume.toLocaleString()}</TableCell>
                            <TableCell align="right" sx={{ color: c.vol_oi_ratio >= 1 ? '#10b981' : '#f9fafb' }}>
                              {c.vol_oi_ratio.toFixed(2)}×
                            </TableCell>
                            <TableCell align="right" sx={{ color: c.otm_pct > 0 ? '#9ca3af' : '#10b981' }}>
                              {c.otm_pct > 0 ? '+' : ''}{c.otm_pct.toFixed(1)}%
                            </TableCell>
                            <TableCell>
                              <Chip size="small" label={c.conviction}
                                sx={{ fontSize: 10, height: 18,
                                  bgcolor: c.conviction === 'very high' ? '#7f1d1d'
                                    : c.conviction === 'high' ? '#78350f'
                                    : '#1f2937',
                                  color: '#f9fafb' }} />
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </Box>
                ) : (
                  <Typography variant="body2" sx={{ color: '#6b7280', fontStyle: 'italic' }}>
                    {flow.unusual_calls.interpretation}
                  </Typography>
                )}
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary">Unusual call data not available.</Typography>
            )}
          </Stack>
        )}
      </Paper>

      {/* ── Section 4: News & Sentiment ─────────────────────────────── */}
      <Paper sx={{ p: 2 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
          <SectionHeader title="Recent News & Sentiment" />
          <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={() => refetchNews()} sx={{ fontSize: 11 }}>
            Refresh
          </Button>
        </Stack>
        {newsLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
        ) : !news || news.articles.length === 0 ? (
          <Typography variant="body2" color="text.secondary">No recent news available.</Typography>
        ) : (
          <Stack spacing={0}>
            {news.sentiment_summary && (
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
                <Typography variant="caption" sx={{ color: '#6b7280' }}>Overall:</Typography>
                <Chip
                  size="small"
                  label={news.sentiment_summary.overall.toUpperCase()}
                  sx={{
                    fontSize: 11, height: 20, fontWeight: 700,
                    bgcolor: news.sentiment_summary.overall === 'positive' ? '#1e3a2f'
                      : news.sentiment_summary.overall === 'negative' ? '#3a1e1e'
                      : '#1f2937',
                    color: news.sentiment_summary.overall === 'positive' ? '#10b981'
                      : news.sentiment_summary.overall === 'negative' ? '#ef4444'
                      : '#9ca3af',
                    border: '1px solid',
                    borderColor: news.sentiment_summary.overall === 'positive' ? '#10b981'
                      : news.sentiment_summary.overall === 'negative' ? '#ef4444'
                      : '#4b5563',
                  }}
                />
                <Typography variant="caption" sx={{ color: '#6b7280' }}>
                  {news.sentiment_summary.positive_count}↑ &nbsp;
                  {news.sentiment_summary.negative_count}↓ &nbsp;
                  {news.sentiment_summary.neutral_count}–
                </Typography>
              </Stack>
            )}
            {news.articles.map((article, i) => (
              <Box key={i}>
                {i > 0 && <Divider sx={{ my: 1, borderColor: '#1f2937' }} />}
                <Stack direction="row" spacing={1} alignItems="flex-start">
                  {article.sentiment && (
                    <Chip
                      size="small"
                      label={article.sentiment === 'positive' ? '↑' : article.sentiment === 'negative' ? '↓' : '–'}
                      title={`${article.sentiment} (${((article.sentiment_score ?? 0) * 100).toFixed(0)}%)`}
                      sx={{
                        fontSize: 12, height: 20, minWidth: 24, mt: 0.2, flexShrink: 0,
                        bgcolor: article.sentiment === 'positive' ? '#1e3a2f'
                          : article.sentiment === 'negative' ? '#3a1e1e'
                          : '#1f2937',
                        color: article.sentiment === 'positive' ? '#10b981'
                          : article.sentiment === 'negative' ? '#ef4444'
                          : '#6b7280',
                      }}
                    />
                  )}
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    {article.url ? (
                      <Link href={article.url} target="_blank" rel="noopener noreferrer"
                        sx={{ color: '#f9fafb', fontSize: 12, fontWeight: 500, textDecoration: 'none',
                          '&:hover': { textDecoration: 'underline' } }}>
                        {article.title}
                      </Link>
                    ) : (
                      <Typography variant="body2" sx={{ fontSize: 12, fontWeight: 500 }}>{article.title}</Typography>
                    )}
                    <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', mt: 0.25 }}>
                      {article.publisher}{article.published ? ` · ${new Date(article.published).toLocaleDateString()}` : ''}
                      {article.sentiment_score != null && (
                        <span style={{ marginLeft: 6, color: '#4b5563' }}>
                          ({((article.sentiment_score) * 100).toFixed(0)}% confidence)
                        </span>
                      )}
                    </Typography>
                  </Box>
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </Paper>

      {/* ── Section 5: Risk / Drawdown ──────────────────────────────── */}
      <Paper sx={{ p: 2 }}>
        <SectionHeader title="Risk & Stop-Loss Calibration" />
        {riskLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>
        ) : riskError ? (
          <SectionError message={(riskError as Error).message ?? 'Failed to load risk signals'} onRetry={refetchRisk} />
        ) : risk?.drawdown ? (
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={3}>
            <Box sx={{ flex: 1 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>Historical Drawdown</Typography>
              <MetricRow label="Worst 1-day drop" value={`${risk.drawdown.max_1day_drawdown_pct.toFixed(2)}%`} color="#ef4444" />
              <MetricRow label="Worst 5-day drop" value={`${risk.drawdown.max_5day_drawdown_pct.toFixed(2)}%`} color="#ef4444" />
              <MetricRow label="Worst intraday" value={`${risk.drawdown.max_intraday_drop_pct.toFixed(2)}%`} color="#f97316" />
              <MetricRow label="Recent 30-bar worst" value={`${risk.drawdown.recent_max_1day_pct.toFixed(2)}%`} color="#f59e0b" />
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>Stop-Loss Recommendation</Typography>
              <Box sx={{ mt: 1, p: 1.5, bgcolor: '#111827', borderRadius: 1, border: '1px solid #374151' }}>
                <Typography variant="body2" sx={{ color: '#10b981', fontWeight: 700, fontSize: 20 }}>
                  {risk.drawdown.trailing_stop_pct.toFixed(1)}%
                </Typography>
                <Typography variant="caption" sx={{ color: '#9ca3af' }}>
                  Minimum trailing stop to avoid noise false-triggers
                </Typography>
              </Box>
              {risk.vwap && (
                <MetricRow label="VWAP" value={`$${risk.vwap.toFixed(2)}`} color="#6366f1" />
              )}
              {risk.vwap_position && (
                <MetricRow label="vs VWAP" value={risk.vwap_position === 'above_vwap' ? 'Above' : 'Below'}
                  color={risk.vwap_position === 'above_vwap' ? '#10b981' : '#ef4444'} />
              )}
            </Box>
            <Box sx={{ flex: 2 }}>
              <Typography variant="caption" sx={{ color: '#6b7280' }}>Context</Typography>
              <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', mt: 0.5, fontStyle: 'italic', lineHeight: 1.5 }}>
                {risk.drawdown.stop_width_note}
              </Typography>
            </Box>
          </Stack>
        ) : (
          <Typography variant="body2" color="text.secondary">Risk data not available for this security.</Typography>
        )}
      </Paper>
    </Stack>
  );
}
