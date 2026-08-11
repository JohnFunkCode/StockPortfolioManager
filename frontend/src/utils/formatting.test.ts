import { describe, expect, it } from 'vitest';

import {
  formatCurrency,
  formatDate,
  formatDateTime,
  formatMarketCap,
  formatPercent,
  formatPercentRaw,
} from './formatting';

describe('formatting utils', () => {
  it('formatCurrency', () => {
    expect(formatCurrency(1234.5)).toBe('$1,234.50');
    expect(formatCurrency(0)).toBe('$0.00');
    expect(formatCurrency(null)).toBe('N/A');
    expect(formatCurrency(undefined)).toBe('N/A');
  });

  it('formatCurrency honours a non-dollar currency', () => {
    expect(formatCurrency(1234.5, 'SEK')).toContain('SEK');
    expect(formatCurrency(1234.5, 'SEK')).not.toContain('$');
    // Empty/unknown falls back to dollars rather than throwing on a bad code.
    expect(formatCurrency(1234.5, '')).toBe('$1,234.50');
  });

  it('formatPercent scales by 100', () => {
    expect(formatPercent(0.1234)).toBe('12.34%');
    expect(formatPercent(0.1234, 1)).toBe('12.3%');
    expect(formatPercent(null)).toBe('N/A');
  });

  it('formatPercentRaw does not scale', () => {
    expect(formatPercentRaw(12.34)).toBe('12.34%');
    expect(formatPercentRaw(12.3456, 1)).toBe('12.3%');
    expect(formatPercentRaw(undefined)).toBe('N/A');
  });

  it('formatDate', () => {
    expect(formatDate('2026-07-24')).toMatch(/2026/);
    expect(formatDate('2026-07-24')).toMatch(/Jul/);
    expect(formatDate(null)).toBe('N/A');
    expect(formatDate('')).toBe('N/A');
  });

  it('formatDate keeps the day a date-only string names, west of Greenwich', () => {
    // `new Date('2026-08-10')` is UTC midnight, which is Aug 9 in any US zone —
    // the earnings strip rendered "Aug 9 · today" for the API's 2026-08-10.
    expect(formatDate('2026-08-10')).toBe('Aug 10, 2026');
    expect(formatDate('2026-01-01')).toBe('Jan 1, 2026');
    // A string carrying a time is a real instant and keeps timezone handling.
    expect(formatDate('2026-08-10T18:00:00Z')).toMatch(/Aug/);
  });

  it('formatDateTime', () => {
    expect(formatDateTime('2026-07-24T12:00:00Z')).toMatch(/2026/);
    expect(formatDateTime(null)).toBe('N/A');
  });

  it('formatMarketCap scales to T/B/M', () => {
    expect(formatMarketCap(2_940_000_000_000)).toBe('$2.94T');
    expect(formatMarketCap(412_800_000_000)).toBe('$412.8B');
    expect(formatMarketCap(918_200_000)).toBe('$918.2M');
  });

  it('formatMarketCap names a non-dollar currency instead of implying dollars', () => {
    // The SK hynix case: ₩1,456T must never render as a dollar figure.
    expect(formatMarketCap(1_456_620_000_000_000, 'KRW')).toBe('KRW 1456.6T');
    expect(formatMarketCap(412_800_000_000, 'usd')).toBe('$412.8B');
  });

  it('formatMarketCap renders unknown as an em dash, not zero', () => {
    expect(formatMarketCap(null)).toBe('—');
    expect(formatMarketCap(undefined)).toBe('—');
    expect(formatMarketCap(0)).toBe('$0');
  });
});
