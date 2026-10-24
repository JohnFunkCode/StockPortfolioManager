import { describe, it, expect } from 'vitest';
import { DARK_NEUTRALS, LIGHT_NEUTRALS } from './themes';

// WCAG 2 relative luminance / contrast ratio, for #rrggbb colours.
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Every foreground the components draw on a tint-* background (#421 review).
const PAIRS: [string, string][] = [
  ['ink-green', 'tint-green'],
  ['ink-red', 'tint-red'],
  ['ink-amber', 'tint-amber'],
  ['ink-blue', 'tint-blue'],
  ['text', 'tint-red-strong'],
  ['text', 'tint-amber-deep'],
  ['text', 'surface'],
  ['on-tint-green', 'tint-green-strong'],
  ['on-tint-red', 'tint-red-strong'],
];

describe('theme neutrals', () => {
  it('defines the same tokens in both themes', () => {
    expect(Object.keys(LIGHT_NEUTRALS).sort()).toEqual(Object.keys(DARK_NEUTRALS).sort());
  });

  it.each(PAIRS)('light mode: %s on %s reaches 4.5:1', (fg, bg) => {
    expect(contrast(LIGHT_NEUTRALS[fg], LIGHT_NEUTRALS[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps the dark theme on the colours it used before the tokens', () => {
    expect(DARK_NEUTRALS['ink-green']).toBe('#10b981');
    expect(DARK_NEUTRALS['ink-red']).toBe('#ef4444');
    expect(DARK_NEUTRALS['ink-amber']).toBe('#f59e0b');
    expect(DARK_NEUTRALS['ink-blue']).toBe('#60a5fa');
    expect(DARK_NEUTRALS['tint-amber-deep']).toBe('#92400e');
  });
});

// A half-converted pair (a tint-* background beside a literal status colour, or a
// literal dark background beside a var(--qc-text) foreground) reads in one theme
// only. Scan every component for the shape the #421 review caught.
const SOURCES = import.meta.glob('./components/**/*.tsx', {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>;

const LITERAL_STATUS = /#(10b981|ef4444|f59e0b|60a5fa|92400e)\b/i;

describe('component colour pairs', () => {
  it('never pairs a tint-* background with a literal status colour', () => {
    const offenders: string[] = [];
    for (const [path, src] of Object.entries(SOURCES)) {
      if (path.includes('.test.')) continue;
      const lines = src.split('\n');
      lines.forEach((line, i) => {
        if (!line.includes('var(--qc-tint-')) return;
        const window = lines.slice(Math.max(0, i - 3), i + 4).join('\n');
        if (LITERAL_STATUS.test(window)) offenders.push(`${path}:${i + 1}`);
      });
    }
    expect(offenders).toEqual([]);
  });
});
