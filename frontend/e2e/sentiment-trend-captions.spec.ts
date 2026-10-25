import { expect, test, type Page } from '@playwright/test';

/**
 * Real-browser geometry check for the sentiment trend's bar captions (#411,
 * #438 review). Their fit is decided by CSS container queries, which jsdom
 * cannot evaluate, so this is the only test that sees the actual layout.
 *
 * The trend route is intercepted with boundary fixtures (nets of ±1.00 and 0,
 * counts up to the compact "999K"/"1M"); everything else reaches the real
 * local API. For every window and a sweep of card widths around the cut-offs,
 * no visible caption may extend past its column: a number is whole or absent.
 */

const NETS = [-1, 1, 0, -0.382, 0.88];
const COUNTS = [34, 999, 1000, 12_345, 999_499, 999_999, 0];

function trend(weeks: number) {
  const monday = Date.UTC(2026, 9, 19); // 2026-10-19
  const buckets = Array.from({ length: weeks }, (_, i) => {
    const start = new Date(monday - (weeks - 1 - i) * 7 * 86_400_000).toISOString().slice(0, 10);
    const count = COUNTS[i % COUNTS.length];
    const net = count ? NETS[i % NETS.length] : null;
    return {
      week_start: start, week_end: start, article_count: count,
      positive_count: 0, negative_count: 0, neutral_count: count,
      net_score: net, label: count ? 'neutral' : null, thin: false,
      in_progress: i === weeks - 1, before_history: false, flip: null,
    };
  });
  return {
    symbol: 'WDC', days: weeks * 7, bucket: 'week', weeks, history_start: '2026-01-05',
    weeks_with_articles: buckets.filter((b) => b.article_count).length,
    total_articles: buckets.reduce((n, b) => n + b.article_count, 0),
    thin_threshold: 10, buckets,
  };
}

async function openTrend(page: Page) {
  await page.route('**/api/securities/WDC/news/trend**', (route) => {
    const days = Number(new URL(route.request().url()).searchParams.get('days'));
    return route.fulfill({ json: trend(days / 7) });
  });
  await page.goto('/securities/WDC');
  await page.getByRole('tab', { name: 'Signals' }).click();
  await expect(page.getByTestId('sentiment-trend-chart')).toBeVisible({ timeout: 30_000 });
  await page.evaluate(() => document.fonts.ready);
}

/** Visible caption text, and any caption whose visible spans overrun it. */
async function measure(page: Page, width: number) {
  return page.evaluate((px) => {
    const card = document.querySelector<HTMLElement>('[data-testid="sentiment-trend"]')!;
    card.style.width = `${px}px`;
    void card.offsetWidth; // force layout before reading geometry
    const out = { shown: [] as string[], overrun: [] as string[] };
    for (const cap of card.querySelectorAll<HTMLElement>('[data-testid="trend-caption"]')) {
      const box = cap.getBoundingClientRect();
      const spans = [...cap.children].filter((s) => getComputedStyle(s).display !== 'none');
      const text = spans.map((s) => s.textContent).join('');
      if (!text) continue;
      out.shown.push(text);
      const right = Math.max(...spans.map((s) => s.getBoundingClientRect().right));
      const left = Math.min(...spans.map((s) => s.getBoundingClientRect().left));
      if (right > box.right + 0.5 || left < box.left - 0.5) out.overrun.push(`${text} @${px}px`);
    }
    return out;
  }, width);
}

// Fixture weeks i pair NETS[i % 5] with COUNTS[i % 7]; each list names the
// widest pairings that window contains (26w reaches week 25: −1.00 with 999K).
const WIDEST: Record<string, string[]> = {
  '4w': ['−1.00 · 34', '+1.00 · 999', '−0.38 · 12K'],
  '13w': ['−1.00 · 34', '+1.00 · 999', '−0.38 · 12K', '+0.88 · 999K'],
  '26w': ['−1.00 · 34', '+1.00 · 999', '−0.38 · 12K', '+0.88 · 999K', '−1.00 · 999K'],
};

for (const window of Object.keys(WIDEST)) {
  test(`captions never overrun their column (${window})`, async ({ page }) => {
    await openTrend(page);
    await page.getByRole('button', { name: window, exact: true }).click();
    const weeks = parseInt(window, 10);
    await expect(page.locator('[data-testid^="trend-week-"]')).toHaveCount(weeks);

    const shown = new Set<string>();
    for (let px = 200; px <= 2000; px += 13) {
      const { shown: s, overrun } = await measure(page, px);
      expect(overrun, `clipped captions at ${px}px`).toEqual([]);
      s.forEach((t) => shown.add(t));
    }
    // Not vacuous: somewhere in the sweep the widest captions appear whole.
    expect([...shown]).toEqual(expect.arrayContaining(WIDEST[window]));
  });
}
