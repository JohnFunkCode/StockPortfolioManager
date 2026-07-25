/**
 * /api/settings client (issue #124): per-user Sidekick settings — currently
 * just the selected chat model, with the server-authoritative model catalog
 * echoed back on every response.
 */
import { apiRequest } from './client';
import type { ChatModel } from '../chat/models';

export interface SettingsView {
  chat_model: string;
  models: ChatModel[];
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
