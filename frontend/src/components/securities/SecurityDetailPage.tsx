import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
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
  TextField,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useTechnicals, useOptionsLatest, useOptionsHistory, useOptionsAnalytics, useIVRank, useEarnings, useBackfillOptionsHistory, useSecurities, useAddSecurity, useRemoveFromPortfolio } from '../../hooks/useSecurities';
import SignalsTab from './SignalsTab';
import PriceChart from './charts/PriceChart';
import RSIChart from './charts/RSIChart';
import MACDChart from './charts/MACDChart';
import VolumeChart from './charts/VolumeChart';
import OptionsChainChart from './charts/OptionsChainChart';
import PCRatioChart from './charts/PCRatioChart';
import MaxPainChart from './charts/MaxPainChart';
import IVTermStructureChart from './charts/IVTermStructureChart';
import ErrorAlert from '../common/ErrorAlert';

function daysToExpiry(expiration: string): number {
  return Math.round((new Date(expiration).getTime() - Date.now()) / 86_400_000);
}

// ---------------------------------------------------------------------------
// Options Performance narrative summary
// ---------------------------------------------------------------------------

interface PcHistPoint { captured_at: string; price: number; put_call_ratio: number | null }

interface PerfExpiration {
  expiration: string;
  put_call_ratio: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  total_call_vol: number | null;
  total_put_vol: number | null;
  avg_call_iv: number | null;
  avg_put_iv: number | null;
}

function buildPerformanceSummary(
  snap: { price: number; expirations: PerfExpiration[] } | null | undefined,
  histPoints: PcHistPoint[],
  aggPC: number | null,
  avgCallIV: number | null,
  avgPutIV: number | null,
): string[] {
  if (!snap) return [];
  const lines: string[] = [];
  const { expirations } = snap;
  if (expirations.length === 0) return [];

  // --- Aggregate P/C sentiment ---
  if (aggPC != null) {
    if (aggPC > 1.5) {
      lines.push(`Aggregate put/call OI ratio is ${aggPC.toFixed(2)} — sharply elevated, reflecting broad bearish hedging or outright put buying across all expirations.`);
    } else if (aggPC > 1.0) {
      lines.push(`Aggregate put/call OI ratio of ${aggPC.toFixed(2)} indicates a net bearish tilt, with more open put positions than calls when summed across all expirations.`);
    } else if (aggPC < 0.5) {
      lines.push(`Aggregate put/call OI ratio is low at ${aggPC.toFixed(2)}, signalling strong bullish positioning with call OI heavily dominating across expirations.`);
    } else if (aggPC < 0.75) {
      lines.push(`Aggregate put/call OI ratio of ${aggPC.toFixed(2)} is modestly call-skewed, suggesting more bullish than bearish positioning overall.`);
    } else {
      lines.push(`Aggregate put/call OI ratio of ${aggPC.toFixed(2)} is broadly neutral, with no strong directional tilt evident across expirations.`);
    }
  }

  // --- IV term structure shape ---
  const sortedByDTE = [...expirations]
    .filter((e) => e.avg_call_iv != null || e.avg_put_iv != null)
    .sort((a, b) => daysToExpiry(a.expiration) - daysToExpiry(b.expiration));

  if (sortedByDTE.length >= 2) {
    const near = sortedByDTE[0];
    const far  = sortedByDTE[sortedByDTE.length - 1];
    const nearIV = ((near.avg_call_iv ?? 0) + (near.avg_put_iv ?? 0)) / 2;
    const farIV  = ((far.avg_call_iv  ?? 0) + (far.avg_put_iv  ?? 0)) / 2;
    const nearDTE = Math.max(0, daysToExpiry(near.expiration));
    const farDTE  = Math.max(0, daysToExpiry(far.expiration));

    if (nearIV > farIV + 5) {
      lines.push(`The IV term structure is in backwardation: near-term options (${nearDTE}d, ${nearIV.toFixed(1)}% avg IV) are priced significantly higher than longer-dated ones (${farDTE}d, ${farIV.toFixed(1)}% avg IV). This typically signals an imminent catalyst — earnings, guidance, or a macro event — driving elevated short-dated premium.`);
    } else if (farIV > nearIV + 3) {
      lines.push(`The IV term structure is in normal contango: longer-dated options (${farDTE}d, ${farIV.toFixed(1)}% avg IV) carry more premium than near-term contracts (${nearDTE}d, ${nearIV.toFixed(1)}% avg IV), consistent with typical time-value behaviour and no immediate event risk.`);
    } else {
      lines.push(`IV term structure is relatively flat from ${nearDTE}d (${nearIV.toFixed(1)}%) to ${farDTE}d (${farIV.toFixed(1)}%), indicating the market is pricing comparable uncertainty across the time horizon.`);
    }
  }

  // --- Most active expiration by volume ---
  const byVol = [...expirations]
    .filter((e) => (e.total_call_vol ?? 0) + (e.total_put_vol ?? 0) > 0)
    .sort((a, b) =>
      ((b.total_call_vol ?? 0) + (b.total_put_vol ?? 0)) -
      ((a.total_call_vol ?? 0) + (a.total_put_vol ?? 0))
    );

  if (byVol.length > 0) {
    const hotExp = byVol[0];
    const totalVol = (hotExp.total_call_vol ?? 0) + (hotExp.total_put_vol ?? 0);
    const callVol = hotExp.total_call_vol ?? 0;
    const putVol  = hotExp.total_put_vol  ?? 0;
    const volBias = callVol > putVol * 1.5 ? 'call-dominated' : putVol > callVol * 1.5 ? 'put-dominated' : 'balanced';
    lines.push(`Trading activity is most concentrated in the ${hotExp.expiration} expiry (${Math.max(0, daysToExpiry(hotExp.expiration))}d) with ${totalVol.toLocaleString()} total contracts traded — volume is ${volBias} (${callVol.toLocaleString()} calls / ${putVol.toLocaleString()} puts).`);
  }

  // --- Highest OI expiration ---
  const byOI = [...expirations]
    .filter((e) => (e.total_call_oi ?? 0) + (e.total_put_oi ?? 0) > 0)
    .sort((a, b) =>
      ((b.total_call_oi ?? 0) + (b.total_put_oi ?? 0)) -
      ((a.total_call_oi ?? 0) + (a.total_put_oi ?? 0))
    );

  if (byOI.length > 0 && byOI[0].expiration !== byVol[0]?.expiration) {
    const topOI = byOI[0];
    const totalOI = (topOI.total_call_oi ?? 0) + (topOI.total_put_oi ?? 0);
    lines.push(`The largest open interest concentration is in the ${topOI.expiration} expiry (${Math.max(0, daysToExpiry(topOI.expiration))}d) with ${totalOI.toLocaleString()} contracts — this is typically a key gamma/delta exposure date for market makers.`);
  }

  // --- Call vs put IV skew direction ---
  if (avgCallIV != null && avgPutIV != null) {
    const diff = avgPutIV - avgCallIV;
    if (diff > 5) {
      lines.push(`Put implied volatility (${avgPutIV.toFixed(1)}% avg) runs ${diff.toFixed(1)}pp above call IV (${avgCallIV.toFixed(1)}% avg) across all expirations — a classic downside skew where tail-risk protection is priced at a premium.`);
    } else if (diff < -5) {
      lines.push(`Call implied volatility (${avgCallIV.toFixed(1)}% avg) exceeds put IV (${avgPutIV.toFixed(1)}% avg) by ${Math.abs(diff).toFixed(1)}pp — an unusual reverse skew suggesting strong speculative call demand or a shortage of call sellers.`);
    } else {
      lines.push(`Call IV (${avgCallIV.toFixed(1)}%) and put IV (${avgPutIV.toFixed(1)}%) are closely matched on average, implying the market is not pricing an asymmetric move in either direction.`);
    }
  }

  // --- P/C trend from history ---
  const validHistory = histPoints.filter((p) => p.put_call_ratio != null);
  if (validHistory.length >= 3) {
    const recent = validHistory.slice(-Math.min(5, validHistory.length));
    const first = recent[0].put_call_ratio!;
    const last  = recent[recent.length - 1].put_call_ratio!;
    const delta = last - first;
    if (delta > 0.2) {
      lines.push(`The P/C ratio has trended up from ${first.toFixed(2)} to ${last.toFixed(2)} over the last ${recent.length} sessions — growing bearish hedging pressure or increasing downside protection demand.`);
    } else if (delta < -0.2) {
      lines.push(`The P/C ratio has fallen from ${first.toFixed(2)} to ${last.toFixed(2)} over the last ${recent.length} sessions — put demand is unwinding, suggesting improving sentiment or reduced fear.`);
    } else {
      lines.push(`The P/C ratio has been stable (${first.toFixed(2)} → ${last.toFixed(2)}) over the last ${recent.length} sessions, with no meaningful shift in overall positioning.`);
    }
  }

  return lines;
}

// ---------------------------------------------------------------------------
// Options Analytics narrative summary
// ---------------------------------------------------------------------------

interface AnalyticsRow {
  expiration: string;
  atm_strike: number | null;
  expected_move_dollar: number;
  expected_move_pct: number;
  upper_bound: number;
  lower_bound: number;
  max_pain: number | null;
  put_call_ratio: number | null;
}

interface IVRankData {
  current_iv: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  iv_52w_high: number | null;
  iv_52w_low: number | null;
  data_points: number;
}

function buildAnalyticsSummary(
  rows: AnalyticsRow[],
  price: number,
  ivRank: IVRankData | null | undefined,
): string[] {
  if (rows.length === 0 && !ivRank) return [];
  const lines: string[] = [];

  // --- IV rank environment ---
  if (ivRank && ivRank.current_iv != null) {
    const iv = ivRank.current_iv;
    const rank = ivRank.iv_rank;
    const pct  = ivRank.iv_percentile;
    if (rank != null && rank >= 80) {
      lines.push(`IV is historically elevated — IV Rank ${rank.toFixed(0)} / IV Percentile ${pct?.toFixed(0) ?? '—'} (current: ${iv.toFixed(1)}%). Options are expensive relative to the past year, favouring premium-selling strategies (covered calls, cash-secured puts, credit spreads).`);
    } else if (rank != null && rank >= 50) {
      lines.push(`IV is in the upper half of its 52-week range — IV Rank ${rank.toFixed(0)} / IV Percentile ${pct?.toFixed(0) ?? '—'} (current: ${iv.toFixed(1)}%). Premium is moderately elevated; both buying and selling strategies are viable depending on directional conviction.`);
    } else if (rank != null && rank < 20) {
      lines.push(`IV is near 52-week lows — IV Rank ${rank.toFixed(0)} / IV Percentile ${pct?.toFixed(0) ?? '—'} (current: ${iv.toFixed(1)}%). Options are cheap; long volatility strategies (debit spreads, long straddles ahead of catalysts) have a favourable risk/reward.`);
    } else if (rank != null) {
      lines.push(`IV Rank of ${rank.toFixed(0)} places current IV (${iv.toFixed(1)}%) in the lower half of its 52-week range — options are reasonably priced with no strong bias toward buying or selling premium.`);
    }
  }

  if (rows.length === 0) return lines;

  // Sort by DTE ascending
  const sorted = [...rows].sort(
    (a, b) => daysToExpiry(a.expiration) - daysToExpiry(b.expiration)
  );
  const nearest = sorted[0];
  const nearDTE = Math.max(0, daysToExpiry(nearest.expiration));

  // --- Nearest expiration expected move ---
  lines.push(
    `The nearest expiration (${nearest.expiration}, ${nearDTE}d) prices in a ±$${nearest.expected_move_dollar.toFixed(2)} move (${nearest.expected_move_pct.toFixed(1)}%), ` +
    `implying a range of $${nearest.lower_bound.toFixed(2)}–$${nearest.upper_bound.toFixed(2)} from the current price of $${price.toFixed(2)}.`
  );

  // --- Max pain proximity for nearest expiry ---
  if (nearest.max_pain != null) {
    const dist = ((nearest.max_pain - price) / price * 100);
    const absD = Math.abs(dist);
    if (absD < 1) {
      lines.push(`Max pain for ${nearest.expiration} is $${nearest.max_pain} — nearly coincident with the current price. Market makers' net delta exposure is minimal here, reducing pinning risk but also limiting a clear gravitational pull.`);
    } else if (absD < 3) {
      lines.push(`Max pain for ${nearest.expiration} is $${nearest.max_pain} (${dist > 0 ? '+' : ''}${dist.toFixed(1)}% from current price). Price is within the typical pinning range; there may be gravitational pressure toward this strike into expiry.`);
    } else {
      lines.push(`Max pain for ${nearest.expiration} sits at $${nearest.max_pain}, ${dist > 0 ? 'above' : 'below'} current price by ${absD.toFixed(1)}%. A ${dist > 0 ? 'rally' : 'decline'} toward max pain would represent the path of maximum options decay for sellers.`);
    }
  }

  // --- Near-term vs longer-dated EM comparison ---
  if (sorted.length >= 2) {
    const far = sorted[sorted.length - 1];
    const farDTE = Math.max(1, daysToExpiry(far.expiration));
    // Annualise EM% to compare apples-to-apples
    const nearAnnualised = nearest.expected_move_pct / Math.sqrt(nearDTE || 1) * Math.sqrt(252);
    const farAnnualised  = far.expected_move_pct  / Math.sqrt(farDTE)           * Math.sqrt(252);
    if (nearAnnualised > farAnnualised * 1.15) {
      lines.push(`Annualised volatility implied by the near-term expiry (${nearest.expiration}) is higher than longer-dated contracts (${far.expiration}), consistent with an elevated short-term risk event driving near-term premium.`);
    } else if (farAnnualised > nearAnnualised * 1.15) {
      lines.push(`Longer-dated options (${far.expiration}) imply higher annualised volatility than near-term contracts, suggesting the market anticipates increasing uncertainty over a longer horizon rather than a near-term catalyst.`);
    }
  }

  // --- Max pain above/below price across expirations ---
  const aboveCount = rows.filter((r) => r.max_pain != null && r.max_pain > price).length;
  const belowCount = rows.filter((r) => r.max_pain != null && r.max_pain < price).length;
  const totalWithPain = aboveCount + belowCount;
  if (totalWithPain >= 2) {
    if (aboveCount > belowCount) {
      lines.push(`Across all expirations, max pain is above the current price in ${aboveCount} of ${totalWithPain} cases, suggesting options market structure broadly favours a drift upward toward expiration.`);
    } else if (belowCount > aboveCount) {
      lines.push(`Max pain is below the current price in ${belowCount} of ${totalWithPain} expirations, implying market maker positioning broadly creates gravitational pull to the downside into expiry dates.`);
    }
  }

  return lines;
}

// ---------------------------------------------------------------------------
// Price & MAs narrative summary
// ---------------------------------------------------------------------------

function buildPriceMASummary(indicators: import('../../api/securitiesTypes').TechnicalIndicator[]): string[] {
  if (indicators.length < 5) return [];
  const lines: string[] = [];
  const latest = indicators.at(-1)!;
  const { close, ma10, ma30, ma50, ma100, ma200, bb_upper, bb_lower, bb_middle } = latest;
  if (close == null) return [];

  // --- MA stack / trend posture ---
  const maOrder: { label: string; value: number | null }[] = [
    { label: 'MA10', value: ma10 }, { label: 'MA30', value: ma30 },
    { label: 'MA50', value: ma50 }, { label: 'MA100', value: ma100 },
    { label: 'MA200', value: ma200 },
  ];
  const definedMAs = maOrder.filter((m) => m.value != null) as { label: string; value: number }[];
  const aboveMAs = definedMAs.filter((m) => close > m.value);
  const belowMAs = definedMAs.filter((m) => close < m.value);

  if (aboveMAs.length === definedMAs.length && definedMAs.length >= 3) {
    lines.push(`Price ($${close.toFixed(2)}) is trading above all ${definedMAs.length} moving averages — a fully bullish MA stack indicating broad trend alignment to the upside.`);
  } else if (belowMAs.length === definedMAs.length && definedMAs.length >= 3) {
    lines.push(`Price ($${close.toFixed(2)}) is below all ${definedMAs.length} moving averages — a fully bearish MA stack with no nearby technical support above.`);
  } else if (aboveMAs.length > belowMAs.length) {
    const below = belowMAs.map((m) => m.label).join(', ');
    lines.push(`Price ($${close.toFixed(2)}) is above most moving averages but still below ${below}, suggesting an uptrend that has not yet fully recovered.`);
  } else if (belowMAs.length > aboveMAs.length) {
    const above = aboveMAs.map((m) => m.label).join(', ');
    lines.push(`Price ($${close.toFixed(2)}) is below most moving averages with only ${above || 'none'} as support — the trend posture is predominantly bearish.`);
  }

  // --- MA200 context ---
  if (ma200 != null) {
    const pct = ((close - ma200) / ma200) * 100;
    if (Math.abs(pct) < 2) {
      lines.push(`Price is testing the 200-day MA ($${ma200.toFixed(2)}) — a key long-term support/resistance level. A decisive close above or below would be a significant signal.`);
    } else if (pct > 0) {
      lines.push(`Price is ${pct.toFixed(1)}% above the 200-day MA ($${ma200.toFixed(2)}), confirming a long-term uptrend.`);
    } else {
      lines.push(`Price is ${Math.abs(pct).toFixed(1)}% below the 200-day MA ($${ma200.toFixed(2)}), consistent with a long-term downtrend or extended correction.`);
    }
  }

  // --- Golden / death cross proximity ---
  if (ma50 != null && ma200 != null) {
    const spread = ((ma50 - ma200) / ma200) * 100;
    if (spread > 0 && spread < 2) {
      lines.push(`The MA50 ($${ma50.toFixed(2)}) is just ${spread.toFixed(1)}% above the MA200 ($${ma200.toFixed(2)}) — a recent golden cross or one approaching, historically a long-term bullish signal.`);
    } else if (spread < 0 && spread > -2) {
      lines.push(`The MA50 ($${ma50.toFixed(2)}) is just ${Math.abs(spread).toFixed(1)}% below the MA200 ($${ma200.toFixed(2)}) — a recent death cross or one approaching, historically a long-term bearish signal.`);
    } else if (spread > 0) {
      lines.push(`MA50 ($${ma50.toFixed(2)}) is above MA200 ($${ma200.toFixed(2)}) by ${spread.toFixed(1)}pp — a golden cross is in effect, supporting the long-term bullish case.`);
    } else {
      lines.push(`MA50 ($${ma50.toFixed(2)}) is below MA200 ($${ma200.toFixed(2)}) by ${Math.abs(spread).toFixed(1)}pp — a death cross is in effect, reinforcing the long-term bearish posture.`);
    }
  }

  // --- Bollinger Band position ---
  if (bb_upper != null && bb_lower != null && bb_middle != null) {
    const bw = ((bb_upper - bb_lower) / bb_middle) * 100;
    if (close >= bb_upper) {
      lines.push(`Price has touched or exceeded the upper Bollinger Band ($${bb_upper.toFixed(2)}) — a statistically extended move that may indicate short-term overbought conditions or the start of a strong breakout.`);
    } else if (close <= bb_lower) {
      lines.push(`Price is at or below the lower Bollinger Band ($${bb_lower.toFixed(2)}) — statistically oversold on a mean-reversion basis, though not a signal in isolation during a strong downtrend.`);
    } else {
      const pct = ((close - bb_lower) / (bb_upper - bb_lower) * 100).toFixed(0);
      lines.push(`Price ($${close.toFixed(2)}) sits ${pct}% of the way through the Bollinger Band range ($${bb_lower.toFixed(2)}–$${bb_upper.toFixed(2)}), near the midline ($${bb_middle.toFixed(2)}).`);
    }
    if (bw < 10) {
      lines.push(`Band width is compressed at ${bw.toFixed(1)}% — a squeeze often precedes a sharp directional expansion.`);
    } else if (bw > 30) {
      lines.push(`Band width is wide at ${bw.toFixed(1)}%, reflecting elevated recent volatility.`);
    }
  }

  return lines;
}

// ---------------------------------------------------------------------------
// Technical Analysis narrative summary
// ---------------------------------------------------------------------------

function buildTechnicalSummary(indicators: import('../../api/securitiesTypes').TechnicalIndicator[]): string[] {
  if (indicators.length < 5) return [];
  const lines: string[] = [];
  const latest = indicators.at(-1)!;
  const { rsi, macd, macd_signal, macd_hist, close } = latest;

  // --- RSI ---
  if (rsi != null) {
    if (rsi >= 80) {
      lines.push(`RSI is at ${rsi.toFixed(1)} — deeply overbought territory. Momentum is strong but risk of mean reversion or pause is elevated.`);
    } else if (rsi >= 70) {
      lines.push(`RSI is at ${rsi.toFixed(1)} — overbought. Price has run hard; watch for divergence or a pullback signal before adding exposure.`);
    } else if (rsi <= 20) {
      lines.push(`RSI is at ${rsi.toFixed(1)} — deeply oversold. Selling pressure may be exhausting; a mean-reversion bounce is statistically likely but confirmation is needed.`);
    } else if (rsi <= 30) {
      lines.push(`RSI is at ${rsi.toFixed(1)} — oversold. Often a precondition for a bounce, though in strong downtrends RSI can remain depressed for extended periods.`);
    } else if (rsi > 50) {
      lines.push(`RSI is at ${rsi.toFixed(1)} — above the midline, consistent with a bullish trend where dips tend to be bought.`);
    } else {
      lines.push(`RSI is at ${rsi.toFixed(1)} — below the midline, consistent with a bearish trend where rallies tend to be sold.`);
    }

    // RSI trend over last 5 bars
    const rsiSeries = indicators.slice(-6, -1).map((d) => d.rsi).filter((v): v is number => v != null);
    if (rsiSeries.length >= 3) {
      const rsiDelta = rsi - rsiSeries[0];
      if (rsiDelta > 10) lines.push(`RSI has risen ${rsiDelta.toFixed(0)} points over the last few sessions — building upward momentum.`);
      else if (rsiDelta < -10) lines.push(`RSI has fallen ${Math.abs(rsiDelta).toFixed(0)} points over the last few sessions — momentum is deteriorating.`);
    }
  }

  // --- MACD ---
  if (macd != null && macd_signal != null) {
    const crossBullish = macd > macd_signal;
    const separation = Math.abs(macd - macd_signal);
    if (crossBullish) {
      lines.push(`MACD (${macd.toFixed(3)}) is above the signal line (${macd_signal.toFixed(3)}) — a bullish configuration${separation < 0.05 ? ', though the lines are close and a bearish cross could follow' : ''}.`);
    } else {
      lines.push(`MACD (${macd.toFixed(3)}) is below the signal line (${macd_signal.toFixed(3)}) — a bearish configuration${separation < 0.05 ? ', though the lines are converging and a bullish cross may be near' : ''}.`);
    }
  }

  // --- MACD histogram momentum ---
  if (macd_hist != null) {
    const histSeries = indicators.slice(-4).map((d) => d.macd_hist).filter((v): v is number => v != null);
    if (histSeries.length >= 3) {
      const increasing = histSeries.every((v, i) => i === 0 || v > histSeries[i - 1]);
      const decreasing = histSeries.every((v, i) => i === 0 || v < histSeries[i - 1]);
      if (macd_hist > 0 && increasing) {
        lines.push(`MACD histogram is positive and expanding — bullish momentum is accelerating.`);
      } else if (macd_hist > 0 && decreasing) {
        lines.push(`MACD histogram is positive but shrinking — bullish momentum is present but fading; watch for a potential cross below the signal line.`);
      } else if (macd_hist < 0 && decreasing) {
        lines.push(`MACD histogram is negative and deepening — bearish momentum is accelerating.`);
      } else if (macd_hist < 0 && increasing) {
        lines.push(`MACD histogram is negative but rising toward zero — bearish momentum is losing steam, a potential bullish cross may be forming.`);
      }
    }
  }

  // --- Volume trend ---
  const recentVols = indicators.slice(-10).map((d) => d.volume).filter((v) => v > 0);
  if (recentVols.length >= 5) {
    const halfLen = Math.floor(recentVols.length / 2);
    const avgRecent = recentVols.slice(-halfLen).reduce((a, b) => a + b, 0) / halfLen;
    const avgPrior  = recentVols.slice(0, halfLen).reduce((a, b) => a + b, 0) / halfLen;
    const volTrend = ((avgRecent - avgPrior) / avgPrior) * 100;
    if (volTrend > 20 && close != null) {
      const priceRecent = indicators.slice(-halfLen).map((d) => d.close).filter((v): v is number => v != null);
      const pricePrior  = indicators.slice(-recentVols.length, -halfLen).map((d) => d.close).filter((v): v is number => v != null);
      const priceUp = priceRecent.length > 0 && pricePrior.length > 0 &&
        priceRecent.at(-1)! > pricePrior[0];
      lines.push(`Volume has risen ${volTrend.toFixed(0)}% over the past several sessions${priceUp ? ' alongside rising prices — a healthy sign of demand-driven accumulation' : ' while price fell — elevated selling pressure or distribution'}.`);
    } else if (volTrend < -20) {
      lines.push(`Volume has declined ${Math.abs(volTrend).toFixed(0)}% recently — drying up participation may signal either consolidation or waning conviction in the current trend.`);
    }
  }

  return lines;
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
  const [addPortfolioOpen, setAddPortfolioOpen] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [addForm, setAddForm] = useState({ purchase_price: '', quantity: '', purchase_date: '' });
  const [addError, setAddError] = useState('');

  const ticker = symbol.toUpperCase();

  const { data: securitiesData } = useSecurities();
  const security = securitiesData?.securities.find((s) => s.symbol === ticker);
  const source = security?.source ?? null;

  const { portfolio: addPortfolio } = useAddSecurity();
  const { mutate: removeFromPortfolio, isPending: removing } = useRemoveFromPortfolio();

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
  const { mutate: backfill, isPending: backfilling, data: backfillResult, reset: resetBackfill } = useBackfillOptionsHistory(ticker);
  const [backfillDays, setBackfillDays] = useState(90);

  const indicators = techData?.indicators ?? [];
  const latest = indicators.at(-1);

  const handleAddToPortfolio = () => {
    setAddError('');
    addPortfolio.mutate(
      {
        symbol: ticker,
        name: security?.name ?? ticker,
        currency: security?.currency ?? 'USD',
        purchase_price: addForm.purchase_price ? Number(addForm.purchase_price) : undefined,
        quantity: addForm.quantity ? Number(addForm.quantity) : undefined,
        purchase_date: addForm.purchase_date || undefined,
      },
      {
        onSuccess: () => { setAddPortfolioOpen(false); setAddForm({ purchase_price: '', quantity: '', purchase_date: '' }); },
        onError: (e) => setAddError((e as Error).message),
      },
    );
  };

  const handleRemoveFromPortfolio = () => {
    removeFromPortfolio(ticker, {
      onSuccess: () => { setRemoveConfirmOpen(false); },
    });
  };

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }} flexWrap="wrap">
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

        <Box sx={{ flex: 1 }} />

        {/* Portfolio actions */}
        {source === 'watchlist' && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<AccountBalanceWalletIcon />}
            onClick={() => { setAddError(''); setAddPortfolioOpen(true); }}
          >
            Add to Portfolio
          </Button>
        )}
        {(source === 'portfolio' || source === 'both') && (
          <Button
            size="small"
            variant="outlined"
            color="error"
            startIcon={<DeleteOutlineIcon />}
            onClick={() => setRemoveConfirmOpen(true)}
          >
            Remove from Portfolio
          </Button>
        )}
      </Stack>

      {techError && <ErrorAlert message={(techError as Error).message} />}

      {/* Add to Portfolio dialog */}
      <Dialog open={addPortfolioOpen} onClose={() => setAddPortfolioOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add {ticker} to Portfolio</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Purchase Price"
              size="small"
              type="number"
              value={addForm.purchase_price}
              onChange={(e) => setAddForm((f) => ({ ...f, purchase_price: e.target.value }))}
              inputProps={{ min: 0, step: 0.01 }}
            />
            <TextField
              label="Quantity"
              size="small"
              type="number"
              value={addForm.quantity}
              onChange={(e) => setAddForm((f) => ({ ...f, quantity: e.target.value }))}
              inputProps={{ min: 0 }}
            />
            <TextField
              label="Purchase Date"
              size="small"
              type="date"
              value={addForm.purchase_date}
              onChange={(e) => setAddForm((f) => ({ ...f, purchase_date: e.target.value }))}
              InputLabelProps={{ shrink: true }}
            />
            {addError && (
              <Typography variant="caption" color="error">{addError}</Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddPortfolioOpen(false)} size="small">Cancel</Button>
          <Button
            variant="contained"
            size="small"
            onClick={handleAddToPortfolio}
            disabled={addPortfolio.isPending}
          >
            {addPortfolio.isPending ? 'Adding…' : 'Add'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Remove from Portfolio confirmation */}
      <Dialog open={removeConfirmOpen} onClose={() => setRemoveConfirmOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Remove from Portfolio</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Remove <strong>{ticker}</strong> from the portfolio? This will delete the position from the CSV. The security will remain on the watchlist if it appears there.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemoveConfirmOpen(false)} size="small">Cancel</Button>
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleRemoveFromPortfolio}
            disabled={removing}
          >
            {removing ? 'Removing…' : 'Remove'}
          </Button>
        </DialogActions>
      </Dialog>

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

          {/* Interpretation */}
          {!techLoading && indicators.length > 0 && (() => {
            const priceLines = buildPriceMASummary(indicators);
            if (priceLines.length === 0) return null;
            return (
              <Paper variant="outlined" sx={{ p: 2, mt: 2, borderColor: 'divider', bgcolor: 'background.default' }}>
                <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>Interpretation</Typography>
                <Stack spacing={0.75}>
                  {priceLines.map((line, i) => (
                    <Typography key={i} variant="body2" sx={{ lineHeight: 1.6 }}>{line}</Typography>
                  ))}
                </Stack>
              </Paper>
            );
          })()}
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

          {/* Interpretation */}
          {!techLoading && indicators.length > 0 && (() => {
            const techLines = buildTechnicalSummary(indicators);
            if (techLines.length === 0) return null;
            return (
              <Paper variant="outlined" sx={{ p: 2, borderColor: 'divider', bgcolor: 'background.default' }}>
                <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>Interpretation</Typography>
                <Stack spacing={0.75}>
                  {techLines.map((line, i) => (
                    <Typography key={i} variant="body2" sx={{ lineHeight: 1.6 }}>{line}</Typography>
                  ))}
                </Stack>
              </Paper>
            );
          })()}

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
      {tab === 3 && (() => {
        const snap = optLatest?.snapshot;
        const expirations = snap?.expirations ?? [];

        // Aggregate OI and IV across all expirations for summary metrics
        const totalCallOI = expirations.reduce((s, e) => s + (e.total_call_oi ?? 0), 0);
        const totalPutOI  = expirations.reduce((s, e) => s + (e.total_put_oi  ?? 0), 0);
        const totalOI     = totalCallOI + totalPutOI;
        const aggPC       = totalCallOI > 0 ? totalPutOI / totalCallOI : null;

        const ivExps = expirations.filter((e) => e.avg_call_iv != null || e.avg_put_iv != null);
        const avgCallIV = ivExps.length
          ? ivExps.reduce((s, e) => s + (e.avg_call_iv ?? 0), 0) / ivExps.length
          : null;
        const avgPutIV = ivExps.length
          ? ivExps.reduce((s, e) => s + (e.avg_put_iv ?? 0), 0) / ivExps.length
          : null;

        const histPoints = optHistory?.history ?? [];

        return (
          <Stack spacing={2}>
            {/* Summary metrics */}
            {snap && (
              <Paper sx={{ p: 2 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
                  <Typography variant="subtitle1">Options Overview</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Snapshot: {snap.captured_at.slice(0, 16).replace('T', ' ')} UTC
                    &nbsp;·&nbsp;${snap.price.toFixed(2)}
                  </Typography>
                </Stack>
                <Stack direction="row" spacing={3} flexWrap="wrap" sx={{ mt: 1 }}>
                  {[
                    { label: 'Expirations', value: expirations.length, color: '#f9fafb' },
                    { label: 'Total Call OI', value: totalCallOI.toLocaleString(), color: '#3b82f6' },
                    { label: 'Total Put OI',  value: totalPutOI.toLocaleString(),  color: '#f59e0b' },
                    { label: 'Agg P/C Ratio', value: aggPC != null ? aggPC.toFixed(2) : '—',
                      color: aggPC == null ? '#6b7280' : aggPC > 1 ? '#f59e0b' : aggPC < 0.7 ? '#3b82f6' : '#f9fafb' },
                    { label: 'Avg Call IV',   value: avgCallIV != null ? `${avgCallIV.toFixed(1)}%` : '—', color: '#3b82f6' },
                    { label: 'Avg Put IV',    value: avgPutIV  != null ? `${avgPutIV.toFixed(1)}%`  : '—', color: '#f59e0b' },
                  ].map(({ label, value, color }) => (
                    <Box key={label}>
                      <Typography variant="caption" sx={{ color: '#6b7280' }}>{label}</Typography>
                      <Typography variant="body1" sx={{ color, fontWeight: 700 }}>{value}</Typography>
                    </Box>
                  ))}
                  {totalOI > 0 && (
                    <Box sx={{ flex: 1, minWidth: 180, alignSelf: 'center' }}>
                      <Typography variant="caption" sx={{ color: '#6b7280' }}>Call / Put OI Split</Typography>
                      <Box sx={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', mt: 0.5 }}>
                        <Box sx={{ width: `${(totalCallOI / totalOI) * 100}%`, bgcolor: '#3b82f6', opacity: 0.8 }} />
                        <Box sx={{ flex: 1, bgcolor: '#f59e0b', opacity: 0.8 }} />
                      </Box>
                      <Stack direction="row" justifyContent="space-between">
                        <Typography variant="caption" sx={{ color: '#3b82f6', fontSize: 9 }}>
                          {((totalCallOI / totalOI) * 100).toFixed(0)}% calls
                        </Typography>
                        <Typography variant="caption" sx={{ color: '#f59e0b', fontSize: 9 }}>
                          {((totalPutOI / totalOI) * 100).toFixed(0)}% puts
                        </Typography>
                      </Stack>
                    </Box>
                  )}
                </Stack>
              </Paper>
            )}

            {/* Narrative summary */}
            {snap && (() => {
              const perfLines = buildPerformanceSummary(snap, histPoints, aggPC, avgCallIV, avgPutIV);
              if (perfLines.length === 0) return null;
              return (
                <Paper variant="outlined" sx={{ p: 2, borderColor: 'divider', bgcolor: 'background.default' }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>
                    Interpretation
                  </Typography>
                  <Stack spacing={0.75}>
                    {perfLines.map((line, i) => (
                      <Typography key={i} variant="body2" sx={{ color: 'text.primary', lineHeight: 1.6 }}>
                        {line}
                      </Typography>
                    ))}
                  </Stack>
                </Paper>
              );
            })()}

            {/* IV Term Structure */}
            {expirations.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="subtitle1" gutterBottom>IV Term Structure</Typography>
                {optLoading ? (
                  <ChartSkeleton height={240} />
                ) : (
                  <>
                    <Stack direction="row" spacing={2} sx={{ mb: 1 }}>
                      <Typography variant="caption" sx={{ color: '#3b82f6' }}>▮ Avg Call IV</Typography>
                      <Typography variant="caption" sx={{ color: '#f59e0b' }}>▮ Avg Put IV</Typography>
                      <Typography variant="caption" sx={{ color: '#a855f7' }}>╌ Composite Avg</Typography>
                    </Stack>
                    <IVTermStructureChart expirations={expirations} height={240} />
                    <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                      X-axis = days to expiry. Elevated near-term IV relative to longer dates indicates
                      backwardation — typically caused by an upcoming event (earnings, Fed, etc.).
                      Normal contango curves rise gradually from left to right.
                    </Typography>
                  </>
                )}
              </Paper>
            )}

            {/* Expiration breakdown table */}
            {expirations.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="subtitle1" gutterBottom>Expiration Breakdown</Typography>
                <Box sx={{ overflowX: 'auto' }}>
                  <Table size="small" sx={{ '& td, & th': { fontSize: 12, py: 0.5 } }}>
                    <TableHead>
                      <TableRow>
                        <TableCell>Expiration</TableCell>
                        <TableCell align="right">DTE</TableCell>
                        <TableCell align="right">Call OI</TableCell>
                        <TableCell align="right">Put OI</TableCell>
                        <TableCell align="right">P/C Ratio</TableCell>
                        <TableCell align="right">Avg Call IV</TableCell>
                        <TableCell align="right">Avg Put IV</TableCell>
                        <TableCell align="right">Call Vol</TableCell>
                        <TableCell align="right">Put Vol</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {expirations.map((e) => {
                        const dte = Math.max(0, Math.round(
                          (new Date(e.expiration).getTime() - Date.now()) / 86_400_000
                        ));
                        return (
                          <TableRow key={e.expiration}>
                            <TableCell>{e.expiration}</TableCell>
                            <TableCell align="right"
                              sx={{ color: dte <= 7 ? '#ef4444' : dte <= 21 ? '#f59e0b' : '#9ca3af' }}>
                              {dte}d
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#3b82f6' }}>
                              {e.total_call_oi?.toLocaleString() ?? '—'}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#f59e0b' }}>
                              {e.total_put_oi?.toLocaleString() ?? '—'}
                            </TableCell>
                            <TableCell align="right">
                              {e.put_call_ratio != null
                                ? <Chip size="small" label={e.put_call_ratio.toFixed(2)}
                                    sx={{ fontSize: 10, height: 18,
                                      bgcolor: e.put_call_ratio > 1 ? '#1e3a2f' : '#1e2a3a',
                                      color:   e.put_call_ratio > 1 ? '#10b981' : '#60a5fa' }} />
                                : '—'}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#3b82f6' }}>
                              {e.avg_call_iv != null ? `${e.avg_call_iv.toFixed(1)}%` : '—'}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#f59e0b' }}>
                              {e.avg_put_iv != null ? `${e.avg_put_iv.toFixed(1)}%` : '—'}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#9ca3af' }}>
                              {e.total_call_vol?.toLocaleString() ?? '—'}
                            </TableCell>
                            <TableCell align="right" sx={{ color: '#9ca3af' }}>
                              {e.total_put_vol?.toLocaleString() ?? '—'}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </Box>
              </Paper>
            )}

            {!snap && !optLoading && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="body2" color="text.secondary">
                  No options snapshot for {ticker}. Run <code>get_stock_price</code> or{' '}
                  <code>get_full_options_chain</code> via MCP to collect data.
                </Typography>
              </Paper>
            )}

            {/* Polygon.io historical backfill */}
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle1" gutterBottom>Historical P/C Backfill via Polygon.io</Typography>
              <Typography variant="caption" sx={{ color: '#9ca3af', display: 'block', mb: 1.5 }}>
                Polygon.io (Starter plan, ~$29/mo) provides 2+ years of historical options snapshots.
                Requires <code>POLYGON_API_KEY</code> in your <code>.env</code> file.
                Each backfill stores end-of-day P/C ratio and IV for all expirations.
              </Typography>
              <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
                <FormControl size="small" sx={{ minWidth: 110 }}>
                  <InputLabel>Days back</InputLabel>
                  <Select
                    value={backfillDays}
                    label="Days back"
                    onChange={(e) => setBackfillDays(Number(e.target.value))}
                    disabled={backfilling}
                  >
                    {[30, 60, 90, 180, 365, 730].map((d) => (
                      <MenuItem key={d} value={d}>{d}d</MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Button
                  size="small"
                  variant="contained"
                  color="secondary"
                  disabled={backfilling}
                  startIcon={backfilling ? <Skeleton variant="circular" width={14} height={14} /> : undefined}
                  onClick={() => { resetBackfill(); backfill({ days: backfillDays }); }}
                >
                  {backfilling ? `Fetching ${backfillDays}d of history…` : `Backfill ${backfillDays} days`}
                </Button>
                {backfillResult && !backfilling && (
                  <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
                    <Chip size="small" label={`${backfillResult.stored} days stored`}
                      sx={{ bgcolor: '#14532d', color: '#86efac', fontSize: 11 }} />
                    {backfillResult.skipped > 0 && (
                      <Chip size="small" label={`${backfillResult.skipped} already existed`}
                        sx={{ bgcolor: '#1f2937', color: '#9ca3af', fontSize: 11 }} />
                    )}
                    {backfillResult.no_data > 0 && (
                      <Chip size="small" label={`${backfillResult.no_data} no data (holidays/pre-listing)`}
                        sx={{ bgcolor: '#1f2937', color: '#9ca3af', fontSize: 11 }} />
                    )}
                    {backfillResult.failed > 0 && (
                      <Chip size="small" label={`${backfillResult.failed} errors`}
                        sx={{ bgcolor: '#7f1d1d', color: '#fca5a5', fontSize: 11 }} />
                    )}
                    {backfillResult.stored > 0 && (
                      <Typography variant="caption" sx={{ color: '#6b7280' }}>
                        Refresh the page to see updated P/C trend below.
                      </Typography>
                    )}
                  </Stack>
                )}
              </Stack>
              {backfillResult && backfillResult.failed > 0 && (
                <Box sx={{ mt: 1.5 }}>
                  {backfillResult.results
                    .filter((r) => r.status === 'error')
                    .slice(0, 3)
                    .map((r) => (
                      <Typography key={r.date} variant="caption" sx={{ color: '#ef4444', display: 'block' }}>
                        {r.date}: {r.error}
                      </Typography>
                    ))}
                </Box>
              )}
            </Paper>

            {/* P/C Ratio history */}
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="subtitle1">Put/Call Ratio History</Typography>
                <FormControl size="small" sx={{ minWidth: 100 }}>
                  <InputLabel>Period</InputLabel>
                  <Select value={pcDays} label="Period" onChange={(e) => setPcDays(Number(e.target.value))}>
                    {[7, 14, 30, 60, 90].map((d) => (
                      <MenuItem key={d} value={d}>{d}d</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Stack>
              {pcLoading ? (
                <ChartSkeleton height={200} />
              ) : histPoints.length >= 2 ? (
                <>
                  <PCRatioChart data={histPoints} height={220} />
                  <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
                    <Typography variant="caption" color="text.secondary">P/C &gt; 1.0 → more put buyers (bearish sentiment)</Typography>
                    <Typography variant="caption" color="text.secondary">P/C &lt; 0.7 → call-dominated (bullish or complacency)</Typography>
                    <Typography variant="caption" color="text.secondary">Extreme readings are often contrarian signals</Typography>
                  </Stack>
                </>
              ) : histPoints.length === 1 && histPoints[0].put_call_ratio != null ? (() => {
                const pt = histPoints[0];
                const pc = pt.put_call_ratio!;
                const sentiment = pc > 1.2 ? 'Bearish' : pc > 1.0 ? 'Mildly Bearish' : pc < 0.6 ? 'Bullish' : pc < 0.8 ? 'Mildly Bullish' : 'Neutral';
                const sentColor = pc > 1.0 ? '#f59e0b' : pc < 0.8 ? '#10b981' : '#9ca3af';
                return (
                  <Stack spacing={2}>
                    <Stack direction="row" spacing={4} alignItems="center" flexWrap="wrap">
                      <Box>
                        <Typography variant="caption" sx={{ color: '#6b7280' }}>Current P/C Ratio</Typography>
                        <Typography variant="h3" sx={{ color: sentColor, fontWeight: 700 }}>{pc.toFixed(2)}</Typography>
                      </Box>
                      <Box>
                        <Typography variant="caption" sx={{ color: '#6b7280' }}>Sentiment</Typography>
                        <Typography variant="h5" sx={{ color: sentColor, fontWeight: 700 }}>{sentiment}</Typography>
                      </Box>
                      <Box>
                        <Typography variant="caption" sx={{ color: '#6b7280' }}>Captured</Typography>
                        <Typography variant="body2" sx={{ color: '#9ca3af' }}>
                          {pt.captured_at.slice(0, 10)}
                        </Typography>
                      </Box>
                      <Box>
                        <Typography variant="caption" sx={{ color: '#6b7280' }}>Price at Capture</Typography>
                        <Typography variant="body2" sx={{ color: '#f9fafb' }}>${pt.price.toFixed(2)}</Typography>
                      </Box>
                    </Stack>
                    <Box sx={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', maxWidth: 400 }}>
                      <Box sx={{ width: `${Math.min(100, (pc / 2) * 100)}%`, bgcolor: sentColor, transition: 'width 0.4s' }} />
                      <Box sx={{ flex: 1, bgcolor: '#1f2937' }} />
                    </Box>
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                      {[
                        { label: 'Extreme put buying', range: '> 1.5', active: pc > 1.5 },
                        { label: 'Put-heavy', range: '1.0–1.5', active: pc >= 1.0 && pc <= 1.5 },
                        { label: 'Neutral', range: '0.8–1.0', active: pc >= 0.8 && pc < 1.0 },
                        { label: 'Call-heavy', range: '0.6–0.8', active: pc >= 0.6 && pc < 0.8 },
                        { label: 'Extreme call buying', range: '< 0.6', active: pc < 0.6 },
                      ].map(({ label, range, active }) => (
                        <Chip
                          key={label}
                          size="small"
                          label={`${label} (${range})`}
                          variant={active ? 'filled' : 'outlined'}
                          sx={{
                            fontSize: 11,
                            bgcolor: active ? (pc > 1.0 ? '#78350f' : pc < 0.8 ? '#14532d' : '#1f2937') : 'transparent',
                            color: active ? '#f9fafb' : '#6b7280',
                            borderColor: '#374151',
                          }}
                        />
                      ))}
                    </Stack>
                    <Typography variant="caption" sx={{ color: '#6b7280' }}>
                      Only 1 snapshot collected. Use the "Refresh All" button on the Securities page daily to build a trend.
                    </Typography>
                  </Stack>
                );
              })() : (
                <Typography variant="body2" color="text.secondary">
                  No P/C history for {ticker} in the last {pcDays} days. Use the "Refresh All" button on the
                  Securities page to collect today's snapshot, then repeat daily to build a trend.
                </Typography>
              )}
            </Paper>
          </Stack>
        );
      })()}

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

            {/* Narrative summary */}
            {(() => {
              const analyticsLines = buildAnalyticsSummary(rows, price, ivRankData);
              if (analyticsLines.length === 0) return null;
              return (
                <Paper variant="outlined" sx={{ p: 2, borderColor: 'divider', bgcolor: 'background.default' }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>
                    Interpretation
                  </Typography>
                  <Stack spacing={0.75}>
                    {analyticsLines.map((line, i) => (
                      <Typography key={i} variant="body2" sx={{ color: 'text.primary', lineHeight: 1.6 }}>
                        {line}
                      </Typography>
                    ))}
                  </Stack>
                </Paper>
              );
            })()}

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
