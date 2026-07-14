/**
 * Streaming client for POST /api/chat (Server-Sent Events).
 *
 * The shared apiRequest() wrapper always calls response.json(), so it cannot
 * stream; this module mirrors its API_BASE / VITE_API_TOKEN conventions but
 * reads the body incrementally via ReadableStream.
 */
import { ApiError } from './client';
import type { ApiChatMessage, ChatInteraction, ChatStreamEvent } from '../chat/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const API_TOKEN = import.meta.env.VITE_API_TOKEN || '';

const KNOWN_EVENTS = new Set(['text', 'tool_status', 'directive', 'error', 'done']);

/**
 * Incremental SSE parser. Feed the previous leftover buffer plus the new
 * chunk; returns decoded events and the new leftover (a partial frame, if any).
 */
export function parseSSEChunk(
  buffer: string,
  chunk: string,
): { events: ChatStreamEvent[]; buffer: string } {
  const events: ChatStreamEvent[] = [];
  const combined = buffer + chunk;
  const parts = combined.split('\n\n');
  const rest = parts.pop() ?? '';

  for (const part of parts) {
    const lines = part.split('\n').filter((l) => l.length > 0 && !l.startsWith(':'));
    if (lines.length === 0) continue;
    const eventLine = lines.find((l) => l.startsWith('event: '));
    const dataLine = lines.find((l) => l.startsWith('data: '));
    if (!eventLine || !dataLine) continue;
    const eventType = eventLine.slice('event: '.length).trim();
    if (!KNOWN_EVENTS.has(eventType)) continue;
    const raw = dataLine.slice('data: '.length);
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw);
    } catch {
      events.push({ type: 'parse_error', raw });
      continue;
    }
    switch (eventType) {
      case 'text':
        events.push({ type: 'text', delta: String(data.delta ?? '') });
        break;
      case 'tool_status':
        events.push({
          type: 'tool_status',
          tool: String(data.tool ?? ''),
          args: (data.args as Record<string, unknown>) ?? {},
          state: (data.state as 'running' | 'done' | 'error') ?? 'running',
        });
        break;
      case 'directive':
        events.push({
          type: 'directive',
          directive: {
            component: String(data.component ?? ''),
            props: (data.props as Record<string, unknown>) ?? {},
            // Only surface the instance id when the wire frame carries one.
            ...(data.component_id ? { componentId: String(data.component_id) } : {}),
          },
        });
        break;
      case 'error':
        events.push({ type: 'error', message: String(data.message ?? 'unknown error') });
        break;
      case 'done':
        events.push({ type: 'done', stop_reason: String(data.stop_reason ?? 'end_turn') });
        break;
    }
  }
  return { events, buffer: rest };
}

/** POST the conversation and invoke onEvent for each decoded stream event. */
export async function streamChat(
  messages: ApiChatMessage[],
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
  interactions?: ChatInteraction[],
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
    },
    body: JSON.stringify({
      messages,
      ...(interactions && interactions.length > 0 ? { interactions } : {}),
    }),
    signal,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(
      (error as { message?: string; error?: string }).message ||
        (error as { error?: string }).error ||
        response.statusText,
      response.status,
    );
  }
  if (!response.body) {
    throw new ApiError('response has no body', response.status);
  }

  const reader = response.body.getReader();
  const abort = () => {
    void reader.cancel();
  };
  signal?.addEventListener('abort', abort);
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      const result = parseSSEChunk(buffer, decoder.decode(value, { stream: true }));
      buffer = result.buffer;
      for (const event of result.events) onEvent(event);
    }
  } finally {
    signal?.removeEventListener('abort', abort);
  }
  if (signal?.aborted) {
    throw new DOMException('The chat stream was aborted.', 'AbortError');
  }
}
