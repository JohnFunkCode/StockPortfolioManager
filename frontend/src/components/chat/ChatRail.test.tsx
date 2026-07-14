import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ChatStreamEvent } from '../../chat/types';

// Controllable stream mock — tests drive events by hand.
let currentOnEvent: ((e: ChatStreamEvent) => void) | null = null;
let resolveStream: (() => void) | null = null;
const streamChatMock = vi.fn(
  (
    _msgs: unknown,
    onEvent: (e: ChatStreamEvent) => void,
    _signal?: unknown,
    _interactions?: unknown,
  ) =>
    new Promise<void>((resolve) => {
      currentOnEvent = onEvent;
      resolveStream = resolve;
    }),
);
vi.mock('../../api/chatStream', () => ({
  streamChat: (...args: Parameters<typeof streamChatMock>) => streamChatMock(...args),
}));

import { ChatProvider } from '../../chat/ChatContext';
import ChatRail from './ChatRail';

function renderRail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ChatProvider>
        <ChatRail />
      </ChatProvider>
    </QueryClientProvider>,
  );
}

async function sendPrompt(text: string) {
  const input = screen.getByTestId('chat-input').querySelector('textarea, input')!;
  await userEvent.type(input as HTMLElement, `${text}{Enter}`);
}

function emit(event: ChatStreamEvent) {
  act(() => currentOnEvent?.(event));
}

function finishStream() {
  act(() => resolveStream?.());
}

beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockClear();
  currentOnEvent = null;
  resolveStream = null;
});

afterEach(cleanup);

describe('ChatRail', () => {
  it('renders the user message and grows assistant text incrementally', async () => {
    renderRail();
    await sendPrompt('How is INTC?');

    expect(screen.getByText('How is INTC?')).toBeInTheDocument();
    expect(streamChatMock).toHaveBeenCalledTimes(1);

    emit({ type: 'text', delta: 'Let me ' });
    expect(screen.getByText(/Let me/)).toBeInTheDocument();
    emit({ type: 'text', delta: 'check.' });
    expect(screen.getByText(/Let me check\./)).toBeInTheDocument();

    emit({ type: 'done', stop_reason: 'end_turn' });
    finishStream();
    await waitFor(() =>
      expect(screen.getByTestId('chat-input').querySelector('textarea, input')).toBeEnabled(),
    );
  });

  it('renders a directive inline after the preceding text', async () => {
    renderRail();
    await sendPrompt('Show INTC signals');
    emit({ type: 'text', delta: 'Here you go.' });
    emit({ type: 'directive', directive: { component: 'signals', props: { ticker: 'INTC' } } });
    expect(screen.getByTestId('directive-signals')).toBeInTheDocument();
    emit({ type: 'done', stop_reason: 'end_turn' });
    finishStream();
  });

  it('marks directive-bearing bubbles full width so charts can fill the rail', async () => {
    renderRail();
    await sendPrompt('Show INTC signals');
    emit({ type: 'text', delta: 'plain text only' });
    const assistant = () => screen.getByTestId('chat-message-assistant');
    expect(assistant().querySelector('[data-fullwidth="true"]')).toBeNull();
    emit({ type: 'directive', directive: { component: 'signals', props: { ticker: 'INTC' } } });
    expect(assistant().querySelector('[data-fullwidth="true"]')).not.toBeNull();
    emit({ type: 'done', stop_reason: 'end_turn' });
    finishStream();
  });

  it('shows a tool activity chip while running and marks it done', async () => {
    renderRail();
    await sendPrompt('RSI on AMD?');
    emit({ type: 'tool_status', tool: 'get_rsi', args: { symbol: 'AMD' }, state: 'running' });
    expect(screen.getByTestId('tool-chip-get_rsi')).toBeInTheDocument();
    emit({ type: 'tool_status', tool: 'get_rsi', args: { symbol: 'AMD' }, state: 'done' });
    emit({ type: 'done', stop_reason: 'end_turn' });
    finishStream();
    // Chip remains as a record of the call but no longer pulses as running.
    expect(screen.getByTestId('tool-chip-get_rsi').dataset.state).toBe('done');
  });

  it('disables the input while streaming and re-enables on error', async () => {
    renderRail();
    await sendPrompt('hello');
    const input = () => screen.getByTestId('chat-input').querySelector('textarea, input')!;
    expect(input()).toBeDisabled();

    emit({ type: 'error', message: 'model exploded' });
    finishStream();
    await waitFor(() => expect(input()).toBeEnabled());
    expect(screen.getByTestId('chat-error').textContent).toContain('model exploded');
  });

  it('persists the conversation and restores it on remount', async () => {
    const first = renderRail();
    await sendPrompt('persist me');
    emit({ type: 'text', delta: 'Saved reply' });
    emit({ type: 'done', stop_reason: 'end_turn' });
    finishStream();
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('hl-chat-messages') ?? '[]');
      expect(stored).toHaveLength(2);
    });
    first.unmount();

    renderRail();
    expect(screen.getByText('persist me')).toBeInTheDocument();
    expect(screen.getByText(/Saved reply/)).toBeInTheDocument();
  });

  it('survives corrupt localStorage without crashing', () => {
    localStorage.setItem('hl-chat-messages', '{definitely not json');
    renderRail();
    expect(screen.getByTestId('chat-input')).toBeInTheDocument();
  });

  it('scrolls the conversation bottom into view when a prompt is submitted', async () => {
    const scrollSpy = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollSpy;
    renderRail();
    await sendPrompt('scroll me down');
    await waitFor(() => expect(scrollSpy).toHaveBeenCalled());
  });

  it('expand toggle flips data-expanded and persists the preference', async () => {
    renderRail();
    const rail = () => screen.getByTestId('chat-rail');
    expect(rail().dataset.expanded).toBe('false');

    await userEvent.click(screen.getByTestId('chat-expand'));
    expect(rail().dataset.expanded).toBe('true');
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('hl-chat-expanded') ?? 'false')).toBe(true),
    );

    await userEvent.click(screen.getByTestId('chat-expand'));
    expect(rail().dataset.expanded).toBe('false');
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('hl-chat-expanded') ?? 'true')).toBe(false),
    );
  });

  it('restores the expanded preference on remount', () => {
    localStorage.setItem('hl-chat-expanded', 'true');
    renderRail();
    expect(screen.getByTestId('chat-rail').dataset.expanded).toBe('true');
  });

  it('serializes prior directives into the history sent to the API', async () => {
    renderRail();
    await sendPrompt('Show INTC signals');
    emit({ type: 'text', delta: 'Here.' });
    emit({ type: 'directive', directive: { component: 'signals', props: { ticker: 'INTC' } } });
    emit({ type: 'done', stop_reason: 'end_turn' });
    finishStream();
    await waitFor(() =>
      expect(screen.getByTestId('chat-input').querySelector('textarea, input')).toBeEnabled(),
    );

    await sendPrompt('And AMD?');
    const history = streamChatMock.mock.calls[1][0] as { role: string; content: string }[];
    const assistantTurn = history.find((m) => m.role === 'assistant');
    expect(assistantTurn?.content).toContain('Here.');
    expect(assistantTurn?.content).toContain('[shown: signals INTC]');
    expect(history[history.length - 1]).toEqual({ role: 'user', content: 'And AMD?' });
  });

  describe('pending interactions (backchannel composer chips)', () => {
    const PENDING = {
      component_id: 'inst-1',
      component: 'spread_payoff',
      action: 'select_strike',
      payload: { strike: 120 },
    };

    it('shows a chip for a queued interaction and sends it with the next message', async () => {
      localStorage.setItem('hl-chat-pending-interactions', JSON.stringify([PENDING]));
      renderRail();

      const chip = screen.getByTestId('pending-interaction');
      expect(chip.textContent).toContain('select strike');
      expect(chip.textContent).toContain('120');

      await sendPrompt('What about this one?');
      expect(streamChatMock.mock.calls[0][3]).toEqual([PENDING]);
      // One-shot context: the chip is gone after the send.
      expect(screen.queryByTestId('pending-interaction')).toBeNull();
      finishStream();
    });

    it('chip delete discards the pending interaction without sending', async () => {
      localStorage.setItem('hl-chat-pending-interactions', JSON.stringify([PENDING]));
      renderRail();

      const chip = screen.getByTestId('pending-interaction');
      await userEvent.click(chip.querySelector('.MuiChip-deleteIcon')!);
      expect(screen.queryByTestId('pending-interaction')).toBeNull();

      await sendPrompt('plain question');
      expect(streamChatMock.mock.calls[0][3]).toEqual([]);
      finishStream();
    });
  });
});
