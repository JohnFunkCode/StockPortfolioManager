import { describe, expect, it } from 'vitest';
import { bsPrice, buildCurves, normalizeIv, payoffAt, spreadValueAt } from './spreadMath';

// bsPrice fixtures cross-checked against the Python reference
// quantcore/analytics/options_math.bs_price (see test_options_math.TestBsPrice).
describe('bsPrice', () => {
  it('matches the textbook call/put fixture', () => {
    expect(bsPrice(100, 100, 1.0, 0.2, 0.05, 'call')).toBeCloseTo(10.4506, 3);
    expect(bsPrice(100, 100, 1.0, 0.2, 0.05, 'put')).toBeCloseTo(5.5735, 3);
  });

  it('satisfies put-call parity', () => {
    const S = 137.42, K = 150, T = 0.35, sigma = 0.61, r = 0.045;
    const call = bsPrice(S, K, T, sigma, r, 'call');
    const put = bsPrice(S, K, T, sigma, r, 'put');
    expect(call - put).toBeCloseTo(S - K * Math.exp(-r * T), 6);
  });

  it('returns intrinsic at expiry', () => {
    expect(bsPrice(110, 100, 0, 0.3, 0.05, 'call')).toBeCloseTo(10, 9);
    expect(bsPrice(90, 100, 0, 0.3, 0.05, 'put')).toBeCloseTo(10, 9);
    expect(bsPrice(90, 100, 0, 0.3, 0.05, 'call')).toBe(0);
  });
});

describe('normalizeIv', () => {
  it('converts percent-style IVs to decimals and passes decimals through', () => {
    expect(normalizeIv(85)).toBeCloseTo(0.85);
    expect(normalizeIv(0.85)).toBeCloseTo(0.85);
    expect(normalizeIv(0)).toBeCloseTo(0);
  });
});

const CALL_SPREAD = {
  kind: 'call' as const,
  debit: 4.94,
  longLeg: { strike: 140, iv: 0.86 },
  shortLeg: { strike: 160, iv: 0.91 },
};

describe('payoffAt (expiration P/L per share)', () => {
  it('is -debit at/below the long strike for a bull call', () => {
    expect(payoffAt(140, CALL_SPREAD)).toBeCloseTo(-4.94, 9);
    expect(payoffAt(100, CALL_SPREAD)).toBeCloseTo(-4.94, 9);
  });

  it('is width - debit at/above the short strike', () => {
    expect(payoffAt(160, CALL_SPREAD)).toBeCloseTo(15.06, 9);
    expect(payoffAt(500, CALL_SPREAD)).toBeCloseTo(15.06, 9);
  });

  it('is zero at breakeven (long strike + debit)', () => {
    expect(payoffAt(144.94, CALL_SPREAD)).toBeCloseTo(0, 9);
  });

  it('handles bear put spreads (long higher-strike put)', () => {
    const putSpread = {
      kind: 'put' as const,
      debit: 3.2,
      longLeg: { strike: 160, iv: 0.9 },
      shortLeg: { strike: 140, iv: 0.85 },
    };
    expect(payoffAt(170, putSpread)).toBeCloseTo(-3.2, 9); // above both: worthless
    expect(payoffAt(130, putSpread)).toBeCloseTo(20 - 3.2, 9); // below both: full width
  });
});

describe('spreadValueAt (theoretical value today)', () => {
  it('stays within [0, width] and P/L within payoff bounds', () => {
    const T = 45 / 365;
    for (const S of [120, 140, 150, 160, 180]) {
      const value = spreadValueAt(S, CALL_SPREAD, T, 0.045);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(20);
    }
  });

  it('converges to the expiration payoff as T approaches 0', () => {
    const nearExpiry = spreadValueAt(150, CALL_SPREAD, 1e-9, 0.045) - CALL_SPREAD.debit;
    expect(nearExpiry).toBeCloseTo(payoffAt(150, CALL_SPREAD), 3);
  });
});

describe('buildCurves', () => {
  it('spans a domain covering both strikes and spot, with aligned samples', () => {
    const { prices, expiry, now } = buildCurves(CALL_SPREAD, 150, 45 / 365);
    expect(prices[0]).toBeLessThan(140);
    expect(prices[prices.length - 1]).toBeGreaterThan(160);
    expect(expiry).toHaveLength(prices.length);
    expect(now).toHaveLength(prices.length);
    // Expiration curve hits its analytic extremes at the domain edges.
    expect(expiry[0]).toBeCloseTo(-4.94, 6);
    expect(expiry[expiry.length - 1]).toBeCloseTo(15.06, 6);
  });

  it('never produces negative domain prices', () => {
    const tiny = {
      kind: 'call' as const,
      debit: 0.5,
      longLeg: { strike: 1, iv: 1.2 },
      shortLeg: { strike: 2, iv: 1.3 },
    };
    const { prices } = buildCurves(tiny, 1.5, 0.1);
    expect(prices[0]).toBeGreaterThanOrEqual(0);
  });
});
