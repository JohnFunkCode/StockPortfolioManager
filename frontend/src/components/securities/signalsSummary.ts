/**
 * The Signals tab's "Interpretation" lines: one plain-English sentence per
 * signal family, built from the payloads the panels already fetched. Each
 * builder returns its line or null, so a missing payload simply drops out.
 */
import type {
  CandlestickPatternsData, GapAnalysisData, HigherLowsData, NewsSentimentSummary, OBVData,
  OptionsFlowResponse, RiskSignalsResponse, StochasticData, TechnicalSignalsResponse, VWAPData,
  VolumeAnalysisData, NewsResponse,
} from '../../api/securitiesTypes';

type Line = string | null;
interface Bias { bull: string[]; bear: string[] }

const plural = (n: number) => (n !== 1 ? 's' : '');
const signed = (n: number) => `${n > 0 ? '+' : ''}${n.toFixed(1)}`;

function stochasticBias(s: StochasticData | null, b: Bias) {
  if (!s) return;
  const sig = s.signal.toLowerCase();
  if (s.k <= 20 || sig.includes('bull') || sig.includes('oversold')) b.bull.push('stochastic oversold');
  else if (s.k >= 80 || sig.includes('bear') || sig.includes('overbought')) b.bear.push('stochastic overbought');
}

function vwapBias(v: VWAPData | null, b: Bias) {
  if (!v) return;
  if (v.position === 'above_vwap') b.bull.push('price above VWAP');
  else b.bear.push('price below VWAP');
  if (v.reclaim_signal) b.bull.push(`VWAP reclaim (${v.reclaim_strength})`);
}

function obvBias(o: OBVData | null, b: Bias) {
  if (!o) return;
  if (o.divergence === 'bullish') b.bull.push(`bullish OBV divergence (${o.divergence_strength})`);
  else if (o.divergence === 'bearish') b.bear.push(`bearish OBV divergence (${o.divergence_strength})`);
  const together = o.obv_trend === o.price_trend ? o.obv_trend : null;
  if (together === 'rising') b.bull.push('OBV and price trending up');
  else if (together === 'falling') b.bear.push('OBV and price trending down');
}

function higherLowsBias(h: HigherLowsData | null, b: Bias) {
  if (!h?.higher_low_pattern) return;
  b.bull.push(`${h.pattern_strength} higher-low structure (${h.consecutive_higher_lows} consecutive)`);
}

function candleBias(c: CandlestickPatternsData | null, b: Bias) {
  if (!c || c.pattern_count <= 0) return;
  const bull = c.patterns_found.filter((p) => p.bias === 'bullish').length;
  const bear = c.patterns_found.filter((p) => p.bias === 'bearish').length;
  if (bull > bear) b.bull.push(`${bull} bullish candlestick pattern${plural(bull)}`);
  else if (bear > bull) b.bear.push(`${bear} bearish candlestick pattern${plural(bear)}`);
}

function collectBias(tech: TechnicalSignalsResponse | undefined): Bias {
  const b: Bias = { bull: [], bear: [] };
  if (!tech) return b;
  stochasticBias(tech.stochastic, b);
  vwapBias(tech.vwap, b);
  obvBias(tech.obv, b);
  higherLowsBias(tech.higher_lows, b);
  candleBias(tech.candlestick_patterns, b);
  return b;
}

function biasLine({ bull, bear }: Bias): Line {
  if (bull.length === 0 && bear.length === 0) return null;
  const net = bull.length - bear.length;
  if (net >= 2) {
    return `Technical picture is broadly bullish (${bull.length} bullish vs ${bear.length} bearish signal${plural(bear.length)}): ${bull.join(', ')}.`;
  }
  if (net <= -2) {
    return `Technical picture is broadly bearish (${bear.length} bearish vs ${bull.length} bullish signal${plural(bull.length)}): ${bear.join(', ')}.`;
  }
  const bullPart = bull.length > 0 ? `bullish: ${bull.join(', ')}` : 'no clear bullish signals';
  const bearPart = bear.length > 0 ? `; bearish: ${bear.join(', ')}` : '';
  return `Technical signals are mixed — ${bullPart}${bearPart}.`;
}

function volumeRatioLine(ratio: number): Line {
  if (ratio >= 2) {
    return `Volume is running ${ratio.toFixed(1)}× the average — unusually high activity that often precedes or confirms a directional move.`;
  }
  if (ratio < 0.5) {
    return `Volume is thin at ${ratio.toFixed(1)}× average — low participation reduces confidence in any price move seen recently.`;
  }
  return null;
}

function climaxLine(v: VolumeAnalysisData): Line {
  const latest = v.climax_events[0];
  if (!latest) return null;
  return `A ${latest.direction}-volume climax event (${latest.volume_ratio}× avg) was recorded on ${latest.date} — these extremes often mark short-term exhaustion points.`;
}

function gapLine(g: GapAnalysisData | null | undefined): Line {
  if (!g || g.unfilled_count <= 0 || g.bounce_targets.length === 0) return null;
  const gapStr = g.bounce_targets.slice(0, 2)
    .map((t) => `$${t.gap_bottom.toFixed(2)}–$${t.gap_top.toFixed(2)} (${signed(t.distance_pct)}%)`)
    .join(', ');
  return `${g.unfilled_count} unfilled gap${plural(g.unfilled_count)} remain as potential reversion targets: ${gapStr}.`;
}

type Daoi = OptionsFlowResponse['delta_adjusted_oi'];

function daoiLine(d: Daoi | undefined): Line {
  if (d?.net_daoi_shares == null) return null;
  const bias = d.mm_hedge_bias === 'buy_on_rally' ? 'buy on rallies' : 'sell on rallies';
  const wall = d.gamma_wall_strike ? `, with a gamma wall at $${d.gamma_wall_strike}` : '';
  const flip = d.delta_flip_strike ? ` and a delta flip point at $${d.delta_flip_strike}` : '';
  return `Delta-adjusted OI shows ${d.net_daoi_shares.toLocaleString()} net shares of market-maker hedge exposure — MMs are positioned to ${bias}${wall}${flip}.`;
}

function unusualLine(u: OptionsFlowResponse['unusual_calls'] | undefined): Line {
  const top = u?.unusual_calls[0];
  if (!u || !top) return null;
  const count = u.unusual_calls.length;
  return `Unusual call activity detected (${count} sweep${plural(count)}, ${u.sweep_signal} signal): the largest is the $${top.strike} strike expiring ${top.expiration} with ${top.volume.toLocaleString()} contracts at ${top.vol_oi_ratio.toFixed(1)}× vol/OI — ${top.conviction} conviction.`;
}

function riskLine(d: RiskSignalsResponse['drawdown'] | undefined): Line {
  if (!d) return null;
  return `Historical drawdown analysis suggests a minimum trailing stop of ${d.trailing_stop_pct.toFixed(1)}% to avoid noise-driven exits (worst single-day drop: ${d.max_1day_drawdown_pct.toFixed(1)}%).`;
}

const NEWS_WORDING: Record<string, { label: string; read: string }> = {
  positive: { label: 'broadly positive', read: 'positive news flow supports bullish momentum' },
  negative: { label: 'broadly negative', read: 'negative headlines may act as a near-term headwind' },
};
const NEWS_NEUTRAL = { label: 'mixed/neutral', read: 'no strong directional bias from news' };

function newsLine(s: NewsSentimentSummary | undefined): Line {
  if (!s || s.scored_count <= 0) return null;
  const { label, read } = NEWS_WORDING[s.overall] ?? NEWS_NEUTRAL;
  return (
    `News sentiment across ${s.scored_count} recent article${plural(s.scored_count)} is ${label} ` +
    `(${s.positive_count} positive, ${s.negative_count} negative, ${s.neutral_count} neutral) — ${read}.`
  );
}

export function buildSignalsSummary(
  tech: TechnicalSignalsResponse | undefined,
  flow: OptionsFlowResponse | undefined,
  risk: RiskSignalsResponse | undefined,
  news: NewsResponse | undefined,
): string[] {
  const volume = tech?.volume_analysis;
  const lines: Line[] = [
    biasLine(collectBias(tech)),
    volume ? volumeRatioLine(volume.last_volume_ratio) : null,
    volume ? climaxLine(volume) : null,
    gapLine(tech?.gap_analysis),
    daoiLine(flow?.delta_adjusted_oi),
    unusualLine(flow?.unusual_calls),
    riskLine(risk?.drawdown),
    newsLine(news?.sentiment_summary),
  ];
  return lines.filter((l): l is string => l !== null);
}
