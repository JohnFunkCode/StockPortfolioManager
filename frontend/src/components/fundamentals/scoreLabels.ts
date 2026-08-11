/**
 * Shared presentation constants for the `/fundamentals` panels (issue #147
 * Part D).
 *
 * Colour only — no thresholds and no arithmetic. The label itself is computed
 * server-side in `quantcore/services/fundamentals.py` and arrives on the row;
 * deciding here what counts as "strong" would be displayed math in the front
 * end, and would drift from the score it is labelling (Rule 8.4).
 */
import { formatPercent } from '../../utils/formatting';

export const GREEN = '#10b981';
export const RED = '#ef4444';
export const MUTED = '#6b7280';
export const DIM = '#4b5563';

/** Matches the watchlist table's palette so one symbol reads the same on both
 * pages. Unknown labels fall through to the neutral grey. */
export const LABEL_COLORS: Record<string, string> = {
  strong: GREEN,
  improving: '#34d399',
  mixed: '#9ca3af',
  weak: RED,
};

export function labelColor(label: string | null | undefined): string {
  return LABEL_COLORS[label ?? 'mixed'] ?? '#9ca3af';
}

/** Coverage arrives as a fraction (0–1) — the share of the scoring inputs the
 * cache actually has for that symbol — so it goes through `formatPercent`,
 * which scales. The returns columns elsewhere arrive already in percent and use
 * `formatPercentRaw`; mixing the two up is the recurring bug in this codebase. */
export function formatCoverage(coverage: number | null | undefined): string {
  return coverage == null ? '—' : formatPercent(coverage, 0);
}
