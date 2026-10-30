/**
 * /api/settings client (issue #124): per-user Sidekick settings — the selected
 * chat model, with the server-authoritative model catalog echoed back on every
 * response, and the daily Sidekick token budget (G2, #461).
 */
import { apiRequest } from './client';
import type { ChatModel } from '../chat/models';

export interface SettingsView {
  chat_model: string;
  models: ChatModel[];
  /** The user's own daily cap; null means the system limit applies. */
  chat_daily_token_budget: number | null;
  /** The system limit; a user cap can only lower it. */
  chat_daily_token_budget_ceiling: number;
  /** Today's (US Eastern) usage, in-flight reservations included. */
  chat_tokens_used_today: number;
  /** What a turn is measured against: the cap, never above the system limit. */
  chat_daily_token_budget_effective: number;
}

export function getSettings(): Promise<SettingsView> {
  return apiRequest<SettingsView>('/api/settings');
}

export function putChatModel(id: string): Promise<SettingsView> {
  return apiRequest<SettingsView>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify({ chat_model: id }),
  });
}

/** Set the daily cap, or with null go back to the system limit. */
export function putChatBudget(budget: number | null): Promise<SettingsView> {
  return apiRequest<SettingsView>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify({ chat_daily_token_budget: budget }),
  });
}
