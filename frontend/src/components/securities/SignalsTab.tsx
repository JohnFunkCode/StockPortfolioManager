/**
 * SignalsTab — Signals tab content for SecurityDetailPage.
 * Shows technical momentum, structure, options flow, and risk signals.
 */
import { Alert, Box, Button, Chip, CircularProgress, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTechnicalSignals, useOptionsFlowSignals, useRiskSignals } from '../../hooks/useSecurities';

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

export default function SignalsTab({ ticker }: Props) {
  const { data: tech, isLoading: techLoading, error: techError, refetch: refetchTech } = useTechnicalSignals(ticker, true);
  const { data: flow, isLoading: flowLoading, error: flowError, refetch: refetchFlow } = useOptionsFlowSignals(ticker, true);
  const { data: risk, isLoading: riskLoading, error: riskError, refetch: refetchRisk } = useRiskSignals(ticker, true);

  return (
    <Stack spacing={2}>

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

      {/* ── Section 4: Risk / Drawdown ──────────────────────────────── */}
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
