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
