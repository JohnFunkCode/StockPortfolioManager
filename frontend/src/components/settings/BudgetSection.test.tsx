/**
 * BudgetSection tests (G2, #461): loading, load error, today's usage against
 * the effective budget, saving and clearing a cap, client-side bounds, and a
 * server rejection surfacing as an error.
 */
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BudgetSection from './BudgetSection';
import * as settingsApi from '../../api/settings';
import type { SettingsView } from '../../api/settings';

const VIEW: SettingsView = {
  chat_model: 'claude-sonnet-5',
  models: [],
  chat_daily_token_budget: null,
  chat_daily_token_budget_ceiling: 2000000,
  chat_tokens_used_today: 125000,
  chat_daily_token_budget_effective: 2000000,
};

describe('BudgetSection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows a spinner while loading', () => {
    vi.spyOn(settingsApi, 'getSettings').mockReturnValue(new Promise(() => {}));
    render(<BudgetSection />);
    expect(screen.getByLabelText('Loading budget')).toBeTruthy();
  });

  it('shows the load error', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockRejectedValue(new Error('network down'));
    render(<BudgetSection />);
    expect(await screen.findByText('network down')).toBeTruthy();
  });

  it("shows today's use against the effective budget and the contact line", async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue(VIEW);
    render(<BudgetSection />);
    expect(await screen.findByText('Used today: 125,000 of 2,000,000 tokens')).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'Budget used today' }).getAttribute('aria-valuenow')).toBe(
      '6',
    );
    expect(screen.getByText(/contact the development team/)).toBeTruthy();
    // No cap set: nothing to clear.
    expect(screen.queryByRole('button', { name: 'Use system limit' })).toBeNull();
  });

  it('saves a lower cap and shows the new effective budget', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue(VIEW);
    const put = vi.spyOn(settingsApi, 'putChatBudget').mockResolvedValue({
      ...VIEW,
      chat_daily_token_budget: 500000,
      chat_daily_token_budget_effective: 500000,
    });
    render(<BudgetSection />);
    const input = await screen.findByLabelText('Your daily limit (tokens)');

    const user = userEvent.setup();
    await user.type(input, '500000');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(put).toHaveBeenCalledWith(500000);
    expect(await screen.findByText('Used today: 125,000 of 500,000 tokens')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use system limit' })).toBeTruthy();
  });

  it('clears a cap with "Use system limit"', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue({
      ...VIEW,
      chat_daily_token_budget: 500000,
      chat_daily_token_budget_effective: 500000,
    });
    const put = vi.spyOn(settingsApi, 'putChatBudget').mockResolvedValue(VIEW);
    render(<BudgetSection />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Use system limit' }));

    expect(put).toHaveBeenCalledWith(null);
    expect(await screen.findByText('Used today: 125,000 of 2,000,000 tokens')).toBeTruthy();
  });

  it('refuses a cap above the system limit before sending it', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue(VIEW);
    const put = vi.spyOn(settingsApi, 'putChatBudget');
    render(<BudgetSection />);
    const input = await screen.findByLabelText('Your daily limit (tokens)');

    const user = userEvent.setup();
    await user.type(input, '3000000');

    expect(screen.getByText('Enter a whole number from 1 to 2,000,000.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    expect(put).not.toHaveBeenCalled();
  });

  it('surfaces a server rejection', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue(VIEW);
    vi.spyOn(settingsApi, 'putChatBudget').mockRejectedValue(new Error('out of range'));
    render(<BudgetSection />);
    const input = await screen.findByLabelText('Your daily limit (tokens)');

    const user = userEvent.setup();
    await user.type(input, '1000');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('out of range')).toBeTruthy();
  });
});
