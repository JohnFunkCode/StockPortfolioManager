import { expect, test } from '@playwright/test';

/**
 * Drives the real UI against the real local API running with CHAT_FAKE=1:
 * the canned script streams "Let me pull up the INTC signals." plus a
 * show_component(signals, INTC) directive through the genuine agent loop,
 * SSE encoding, stream client, registry validation, and DirectiveRenderer.
 * The rendered SignalsTab then hits the real REST API for data (structural
 * assertions only — the test DB may be empty, and loading/error states still
 * prove the directive path).
 */

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('core flow: open rail, send message, streamed text + live component render', async ({
  page,
}) => {
  await page.getByTestId('chat-toggle').click();
  await expect(page.getByTestId('chat-rail')).toBeVisible();

  const input = page.getByTestId('chat-input').locator('textarea').first();
  await input.fill("How's INTC looking?");
  await input.press('Enter');

  // User bubble appears immediately.
  await expect(page.getByTestId('chat-message-user')).toContainText("How's INTC looking?");

  // Canned stream text arrives.
  await expect(page.getByTestId('chat-message-assistant')).toContainText(
    'Let me pull up the INTC signals.',
    { timeout: 15_000 },
  );

  // The directive rendered a live registry component inline.
  await expect(page.getByTestId('directive-signals')).toBeVisible({ timeout: 15_000 });

  // Closing text after the tool round-trip.
  await expect(page.getByTestId('chat-message-assistant')).toContainText(
    'the panel below is live',
    { timeout: 15_000 },
  );
});

test('fullscreen expand hides page content and still chats; collapse restores layout', async ({
  page,
}) => {
  await page.getByTestId('chat-toggle').click();
  await expect(page.getByTestId('page-content')).toBeVisible();

  // Expand: rail fills the content area, page hidden (but still mounted).
  await page.getByTestId('chat-expand').click();
  await expect(page.getByTestId('chat-rail')).toHaveAttribute('data-expanded', 'true');
  await expect(page.getByTestId('page-content')).toBeHidden();

  // Chat works fullscreen.
  const input = page.getByTestId('chat-input').locator('textarea').first();
  await input.fill('Fullscreen check');
  await input.press('Enter');
  await expect(page.getByTestId('chat-message-assistant')).toContainText(
    'the panel below is live',
    { timeout: 15_000 },
  );

  // Collapse: side-by-side layout returns.
  await page.getByTestId('chat-expand').click();
  await expect(page.getByTestId('chat-rail')).toHaveAttribute('data-expanded', 'false');
  await expect(page.getByTestId('page-content')).toBeVisible();
});

test('backchannel: chart click queues an interaction that rides the next message', async ({
  page,
}) => {
  // The spread card fetches real pricing over REST; the test DB has no
  // contracts, so fulfill that one route with a fixture — everything else
  // (SSE stream, agent loop, interaction validation) is the real backend.
  await page.route('**/options/vertical-spread*', (route) =>
    route.fulfill({
      json: {
        symbol: 'WMT',
        expiration: '2099-12-19',
        kind: 'call',
        debit: 2.1,
        mid_debit: 2.0,
        max_profit: 3.0,
        max_loss: 2.0,
        breakeven: 122.0,
        risk_reward: 1.5,
        warnings: [],
        legs: {
          long: { strike: 120, mid: 6.0, iv: 0.3 },
          short: { strike: 125, mid: 4.0, iv: 0.28 },
        },
      },
    }),
  );

  await page.getByTestId('chat-toggle').click();
  const input = page.getByTestId('chat-input').locator('textarea').first();
  await input.fill('Price a WMT 120/125 call spread');
  await input.press('Enter');

  // The spread_payoff directive rendered an interactive risk graph.
  await expect(page.getByTestId('directive-spread_payoff')).toBeVisible({ timeout: 15_000 });
  const chart = page.getByTestId('spread-payoff-chart');
  await expect(chart).toBeVisible();
  await expect(page.getByTestId('chat-message-assistant').last()).toContainText(
    'risk graph below is live',
    { timeout: 15_000 },
  );

  // Click the chart: a strike is selected, the composer chip and the
  // message-mode reprice affordances appear.
  await chart.click({ position: { x: 200, y: 120 } });
  await expect(page.getByTestId('pending-interaction')).toBeVisible();
  await expect(page.getByTestId('pending-interaction')).toContainText('select strike');
  await expect(page.getByTestId('reprice-long')).toBeVisible();

  // The next typed message carries the interaction in the POST body.
  const chatRequest = page.waitForRequest(
    (r) => r.url().includes('/api/chat') && r.method() === 'POST',
  );
  await input.fill('What about this strike?');
  await input.press('Enter');
  const body = (await chatRequest).postDataJSON() as {
    interactions?: { component: string; action: string; payload: { strike: number } }[];
  };
  expect(body.interactions).toHaveLength(1);
  expect(body.interactions![0].component).toBe('spread_payoff');
  expect(body.interactions![0].action).toBe('select_strike');
  expect(Number.isFinite(body.interactions![0].payload.strike)).toBe(true);

  // Round trip: the REAL backend validated the interaction and the fake
  // acknowledged it; the one-shot chip is gone.
  await expect(page.getByTestId('chat-message-assistant').last()).toContainText(
    'Noted your selection',
    { timeout: 15_000 },
  );
  await expect(page.getByTestId('pending-interaction')).toHaveCount(0);
});

test('conversation persists across navigation and reload', async ({ page }) => {
  await page.getByTestId('chat-toggle').click();
  const input = page.getByTestId('chat-input').locator('textarea').first();
  await input.fill('Persistence check');
  await input.press('Enter');
  await expect(page.getByTestId('chat-message-assistant')).toContainText(
    'the panel below is live',
    { timeout: 15_000 },
  );

  // Navigate to another page — the rail and conversation survive.
  await page.getByRole('link', { name: 'Securities' }).click();
  await expect(page).toHaveURL(/securities/);
  await expect(page.getByTestId('chat-rail')).toBeVisible();
  await expect(page.getByTestId('chat-message-user')).toContainText('Persistence check');

  // Full reload — history restored from localStorage.
  await page.reload();
  await expect(page.getByTestId('chat-rail')).toBeVisible();
  await expect(page.getByTestId('chat-message-user')).toContainText('Persistence check');
  await expect(page.getByTestId('directive-signals')).toBeVisible();
});
