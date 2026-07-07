/**
 * Rendering-side option math for the spread payoff chart. This is a TS twin
 * of the Python reference in quantcore/analytics/options_math.py (bs_price) —
 * used only to draw curves; all tradable numbers (debit, max profit/loss,
 * breakeven) come from the backend's price_vertical_spread response.
 */

export type OptionKind = 'call' | 'put';

export interface SpreadLegInput {
  strike: number;
  /** Implied vol — accepts decimal (0.86) or percent (86) form. */
  iv: number;
}

export interface SpreadInput {
  kind: OptionKind;
  /** Net debit paid per share (mid). */
  debit: number;
  longLeg: SpreadLegInput;
  shortLeg: SpreadLegInput;
}

/** Abramowitz–Stegun-style normal CDF (same shape as the Python norm_cdf). */
function normCdf(x: number): number {
  return 0.5 * (1 + erf(x / Math.SQRT2));
}

function erf(x: number): number {
  // Numerical approximation, |error| < 1.5e-7 — plenty for pixel positions.
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t *
      Math.exp(-ax * ax);
  return sign * y;
}

/** IVs arrive in percent form from some endpoints; normalize to a decimal. */
export function normalizeIv(iv: number): number {
  if (!Number.isFinite(iv) || iv <= 0) return 0;
  return iv > 3 ? iv / 100 : iv;
}

/** Black–Scholes European option price (mirrors Python bs_price). */
export function bsPrice(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number,
  kind: OptionKind,
): number {
  if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) {
    if (T <= 0) return kind === 'call' ? Math.max(S - K, 0) : Math.max(K - S, 0);
    const discountedK = K * Math.exp(-r * Math.max(T, 0));
    return kind === 'call' ? Math.max(S - discountedK, 0) : Math.max(discountedK - S, 0);
  }
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  const call = S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2);
  return kind === 'call' ? call : call - S + K * Math.exp(-r * T);
}

function intrinsic(S: number, K: number, kind: OptionKind): number {
  return kind === 'call' ? Math.max(S - K, 0) : Math.max(K - S, 0);
}

/** P/L per share at expiration for a debit vertical (long leg − short leg − debit). */
export function payoffAt(S: number, spread: SpreadInput): number {
  return (
    intrinsic(S, spread.longLeg.strike, spread.kind) -
    intrinsic(S, spread.shortLeg.strike, spread.kind) -
    spread.debit
  );
}

/** Theoretical spread VALUE (not P/L) per share today, BS-priced per leg. */
export function spreadValueAt(S: number, spread: SpreadInput, T: number, r: number): number {
  const longVal = bsPrice(S, spread.longLeg.strike, T, normalizeIv(spread.longLeg.iv), r, spread.kind);
  const shortVal = bsPrice(S, spread.shortLeg.strike, T, normalizeIv(spread.shortLeg.iv), r, spread.kind);
  return longVal - shortVal;
}

export interface SpreadCurves {
  prices: number[];
  /** P/L per share at expiration. */
  expiry: number[];
  /** P/L per share if closed today at theoretical value. */
  now: number[];
}

export function buildCurves(
  spread: SpreadInput,
  spot: number,
  T: number,
  r = 0.045,
  samples = 121,
): SpreadCurves {
  const kLow = Math.min(spread.longLeg.strike, spread.shortLeg.strike);
  const kHigh = Math.max(spread.longLeg.strike, spread.shortLeg.strike);
  const span = Math.max(kHigh - kLow, kHigh * 0.05);
  const lo = Math.max(0, Math.min(kLow, spot || kLow) - span * 0.9);
  const hi = Math.max(kHigh, spot || kHigh) + span * 0.9;

  const prices: number[] = [];
  const expiry: number[] = [];
  const now: number[] = [];
  for (let i = 0; i < samples; i++) {
    const S = lo + ((hi - lo) * i) / (samples - 1);
    prices.push(S);
    expiry.push(payoffAt(S, spread));
    now.push(spreadValueAt(S, spread, T, r) - spread.debit);
  }
  return { prices, expiry, now };
}
