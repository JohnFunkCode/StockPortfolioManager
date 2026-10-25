import { describe, expect, it } from 'vitest';

import { AMBER, GREEN, RED, biasStyle, signalColor, signedFixed, strengthColor } from './signalsShared';

describe('signalsShared lookups', () => {
  it('maps strengths, with faint for none or missing', () => {
    expect(strengthColor('strong')).toBe(GREEN);
    expect(strengthColor('moderate')).toBe(AMBER);
    expect(strengthColor('weak')).toBe('#f97316');
    expect(strengthColor('none')).toBe('var(--qc-faint)');
    expect(strengthColor(undefined)).toBe('var(--qc-faint)');
    expect(strengthColor('other')).toBe('var(--qc-muted)');
  });

  it('maps signal words first-match-wins, case-insensitively', () => {
    expect(signalColor('Rising')).toBe(GREEN);
    expect(signalColor('falling')).toBe(RED);
    expect(signalColor('strong bearish')).toBe(GREEN); // 'strong' is checked before 'bear'
    expect(signalColor('flat')).toBe('var(--qc-faint)');
    expect(signalColor('oversold')).toBe(AMBER);
    expect(signalColor(undefined)).toBe('var(--qc-faint)');
  });

  it('signs numbers like the old inline ternaries', () => {
    expect(signedFixed(1.24)).toBe('+1.2');
    expect(signedFixed(-2)).toBe('-2.0');
    expect(signedFixed(0)).toBe('0.0');
    expect(signedFixed(3.456, 2)).toBe('+3.46');
  });

  it('styles bias chips with ink on tint', () => {
    expect(biasStyle('bullish')).toEqual({ bg: 'var(--qc-tint-green)', fg: 'var(--qc-ink-green)' });
    expect(biasStyle('bearish')).toEqual({ bg: 'var(--qc-tint-red)', fg: 'var(--qc-ink-red)' });
    expect(biasStyle('neutral')).toEqual({ bg: 'var(--qc-surface)', fg: 'var(--qc-muted)' });
  });
});
