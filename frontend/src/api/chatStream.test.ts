import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseSSEChunk, streamChat } from './chatStream';
import type { ChatStreamEvent } from '../chat/types';

const frame = (event: string, data: object) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe('parseSSEChunk', () => {
  it('parses a single complete frame', () => {
    const { events, buffer } = parseSSEChunk('', frame('text', { delta: 'hi' }));
    expect(events).toEqual([{ type: 'text', delta: 'hi' }]);
    expect(buffer).toBe('');
  });

  it('carries a frame split across two chunks', () => {
    const whole = frame('directive', { component: 'signals', props: { ticker: 'INTC' } });
    const first = parseSSEChunk('', whole.slice(0, 25));
    expect(first.events).toEqual([]);
    const second = parseSSEChunk(first.buffer, whole.slice(25));
    expect(second.events).toEqual([
      { type: 'directive', directive: { component: 'signals', props: { ticker: 'INTC' } } },
    ]);
    expect(second.buffer).toBe('');
  });

  it('parses two frames arriving in one chunk', () => {
    const chunk = frame('text', { delta: 'a' }) + frame('done', { stop_reason: 'end_turn' });
    const { events } = parseSSEChunk('', chunk);
    expect(events).toEqual([
      { type: 'text', delta: 'a' },
      { type: 'done', stop_reason: 'end_turn' },
    ]);
  });

  it('ignores comment keepalives and blank frames', () => {
    const chunk = ': ping\n\n' + frame('text', { delta: 'x' }) + '\n\n';
    const { events } = parseSSEChunk('', chunk);
    expect(events).toEqual([{ type: 'text', delta: 'x' }]);
  });

  it('skips unknown event types without throwing', () => {
    const chunk = frame('telemetry', { blah: 1 }) + frame('text', { delta: 'y' });
    const { events } = parseSSEChunk('', chunk);
    expect(events).toEqual([{ type: 'text', delta: 'y' }]);
  });

  it('surfaces malformed JSON as parse_error and keeps parsing', () => {
    const chunk = 'event: text\ndata: {not json\n\n' + frame('text', { delta: 'ok' });
    const { events } = parseSSEChunk('', chunk);
    expect(events[0].type).toBe('parse_error');
    expect(events[1]).toEqual({ type: 'text', delta: 'ok' });
  });

  it('maps all wire event types', () => {
    const chunk =
      frame('text', { delta: 't' }) +
      frame('tool_status', { tool: 'get_rsi', args: { symbol: 'AMD' }, state: 'running' }) +
      frame('directive', { component: 'live_price', props: { ticker: 'ZS' } }) +
      frame('error', { message: 'boom' }) +
      frame('done', { stop_reason: 'end_turn' });
    const { events } = parseSSEChunk('', chunk);
    expect(events.map((e) => e.type)).toEqual([
      'text',
      'tool_status',
      'directive',
      'error',
      'done',
    ]);
  });
});

function sseResponse(frames: string[], { ok = true, status = 200 } = {}) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(encoder.encode(f));
      controller.close();
    },
  });
  return {
    ok,
    status,
    statusText: ok ? 'OK' : 'Server Error',
    body,
    json: async () => ({ error: 'nope' }),
  } as unknown as Response;
}

describe('streamChat', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs to /api/chat and delivers events in order', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        frame('text', { delta: 'Hello' }),
        frame('directive', { component: 'signals', props: { ticker: 'INTC' } }),
        frame('done', { stop_reason: 'end_turn' }),
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);

    const events: ChatStreamEvent[] = [];
    await streamChat([{ role: 'user', content: 'hi' }], (e) => events.push(e));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/chat');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body).messages).toEqual([{ role: 'user', content: 'hi' }]);

    expect(events.map((e) => e.type)).toEqual(['text', 'directive', 'done']);
  });

  it('rejects with status on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([], { ok: false, status: 500 })));
    await expect(streamChat([{ role: 'user', content: 'hi' }], () => {})).rejects.toMatchObject({
      status: 500,
    });
  });

  it('stops delivering events after abort', async () => {
    const encoder = new TextEncoder();
    let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controllerRef = c;
        c.enqueue(encoder.encode(frame('text', { delta: 'first' })));
        // stream intentionally left open
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, body } as unknown as Response),
    );

    const abort = new AbortController();
    const events: ChatStreamEvent[] = [];
    const run = streamChat(
      [{ role: 'user', content: 'hi' }],
      (e) => {
        events.push(e);
        abort.abort();
      },
      abort.signal,
    );

    await expect(run).rejects.toMatchObject({ name: 'AbortError' });
    expect(events).toHaveLength(1);
    expect(controllerRef).not.toBeNull();
  });
});
