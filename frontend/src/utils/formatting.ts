/**
 * A money figure in its own currency, dollars by default.
 *
 * The `currency` argument exists for the same reason `formatMarketCap` has one:
 * a watchlist price is quoted in the security's trading currency, and stamping
 * a `$` on a Stockholm or Seoul quote is a wrong number rather than a missing
 * one. Callers with a genuinely dollar figure can keep omitting it.
 */
export function formatCurrency(
  value: number | null | undefined,
  currency?: string | null,
): string {
  if (value == null) return 'N/A';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: (currency || 'USD').toUpperCase(),
  }).format(value);
}

export function formatPercent(value: number | null | undefined, decimals = 2): string {
  if (value == null) return 'N/A';
  return `${(value * 100).toFixed(decimals)}%`;
}

export function formatPercentRaw(value: number | null | undefined, decimals = 2): string {
  if (value == null) return 'N/A';
  return `${value.toFixed(decimals)}%`;
}

/**
 * A calendar date, `Aug 10, 2026`.
 *
 * A bare `YYYY-MM-DD` is parsed **component-wise into local time**, not handed
 * to `new Date()`, which reads a date-only string as UTC midnight — one day
 * early for every viewer west of Greenwich. That is not a rounding nicety: the
 * earnings strip showed "Aug 9" beside "today" for a date the API had given as
 * 2026-08-10, and the watchlist's `prices <date>` chip was a day behind all
 * afternoon. Strings carrying a time (`…T12:00:00Z`) are real instants and keep
 * their timezone handling.
 */
export function formatDate(dateString: string | null | undefined): string {
  if (!dateString) return 'N/A';
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateString);
  if (dateOnly) {
    const [, y, m, d] = dateOnly;
    return new Date(Number(y), Number(m) - 1, Number(d)).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(dateString: string | null | undefined): string {
  if (!dateString) return 'N/A';
  return new Date(dateString).toLocaleString('en-US');
}

/** Fractional shares to at most 4 decimals, trailing zeros trimmed. */
export function formatShares(value: number | null | undefined): string {
  if (value == null) return 'N/A';
  return value.toFixed(4).replace(/\.?0+$/, '') || '0';
}

/** Dollars-per-day is null when days held is 0 — render that as em dash, not N/A. */
export function formatDollarsPerDay(value: number | null | undefined): string {
  if (value == null) return '—';
  return formatCurrency(value);
}

/**
 * Market cap as `$2.94T` / `$412.8B` / `$918.2M`, or with a currency code in
 * front of the unit when it is not dollars (`KRW 1,456.6T`).
 *
 * The currency argument is not decoration. A cap is quoted in the security's
 * trading currency, and rendering ₩1,456T as `$1.46T` is a wrong number rather
 * than a missing one — the exact bug that put SK hynix at the top of a dollar
 * column. Callers pass the figure's own currency; a null figure is an em dash,
 * because "not known" and zero are different answers.
 */
export function formatMarketCap(
  value: number | null | undefined,
  currency?: string | null,
): string {
  if (value == null) return '—';

  const abs = Math.abs(value);
  const [scaled, unit] =
    abs >= 1e12 ? [value / 1e12, 'T']
      : abs >= 1e9 ? [value / 1e9, 'B']
        : abs >= 1e6 ? [value / 1e6, 'M']
          : [value, ''];

  const digits = Math.abs(scaled) >= 100 ? 1 : 2;
  const figure = `${scaled.toFixed(unit ? digits : 0)}${unit}`;

  const code = (currency || 'USD').toUpperCase();
  return code === 'USD' ? `$${figure}` : `${code} ${figure}`;
}
