/**
 * /api/settings client tests (issue #124): GET parses `chat_model` + `models`,
 * PUT sends the right wire shape, and FastAPI `{detail}` errors surface as
 * the ApiError message.
 *
 * Runs the real api client against a stubbed global fetch so the whole
 * request path — URL, method, body, error extraction — is under test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './client';
import { getSettings, putChatModel, type SettingsView } from './settings';

const SETTINGS: SettingsView = {
  chat_model: 'claude-sonnet-5',
  models: [
    { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', description: 'Recommended daily driver.' },
    { id: 'claude-opus-4-8', name: 'Claude Opus 4.8', description: 'Heavy-lifting flagship.' },
    { id: 'claude-fable-5', name: 'Claude Fable 5', description: 'Most capable general model.' },
  ],
};

/** A fresh Response per call — bodies are single-use. */
function respondWith(body: unknown, status = 200): () => Promise<Response> {
  return () =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
}

describe('settings api client', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('getSettings GETs and parses chat_model + models', async () => {
    fetchMock.mockImplementation(respondWith(SETTINGS));

    const result = await getSettings();
    expect(result).toEqual(SETTINGS);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/settings');
    expect(options?.method).toBeUndefined();
  });

  it('putChatModel PUTs the selected id and returns the updated settings', async () => {
    const updated = { ...SETTINGS, chat_model: 'claude-opus-4-8' };
    fetchMock.mockImplementation(respondWith(updated));

    const result = await putChatModel('claude-opus-4-8');
    expect(result).toEqual(updated);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/settings');
    expect(options.method).toBe('PUT');
    expect(JSON.parse(options.body)).toEqual({ chat_model: 'claude-opus-4-8' });
  });

  it("surfaces FastAPI's {detail} error copy as the ApiError message", async () => {
    fetchMock.mockImplementation(respondWith({ detail: 'chat_model must be one of the allowed models' }, 400));

    const err = await putChatModel('gpt-4o').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('chat_model must be one of the allowed models');
    expect((err as ApiError).status).toBe(400);
  });
});
