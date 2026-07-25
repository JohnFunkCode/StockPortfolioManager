import { describe, expect, it } from 'vitest';

import {
  formatCurrency,
  formatDate,
  formatDateTime,
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

  it('formatDateTime', () => {
    expect(formatDateTime('2026-07-24T12:00:00Z')).toMatch(/2026/);
    expect(formatDateTime(null)).toBe('N/A');
  });
});
