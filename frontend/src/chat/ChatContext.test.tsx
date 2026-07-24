import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';

import { ChatProvider, serializeForApi, useChat } from './ChatContext';
import type { ChatMessage } from './types';

function wrapper({ children }: { children: ReactNode }) {
  return <ChatProvider>{children}</ChatProvider>;
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe('serializeForApi', () => {
  it('flattens text and directive segments; drops tool_status', () => {
    const messages: ChatMessage[] = [
      {
        role: 'assistant',
        segments: [
          { type: 'text', text: 'Here.' },
          { type: 'directive', directive: { component: 'signals', props: { ticker: 'INTC' } } },
          { type: 'tool_status', tool: 'get_rsi', state: 'done' },
        ],
      },
    ];
    const out = serializeForApi(messages);
    expect(out[0].content).toContain('Here.');
    expect(out[0].content).toContain('[shown: signals INTC]');
    expect(out[0].content).not.toContain('get_rsi');
  });

  it('omits empty messages', () => {
    const out = serializeForApi([{ role: 'assistant', segments: [] }]);
    expect(out).toEqual([]);
  });
});

describe('ChatProvider persistence + controls', () => {
  it('persists rail open/expanded to localStorage', () => {
    const { result } = renderHook(() => useChat(), { wrapper });
    act(() => result.current.setRailOpen(true));
    act(() => result.current.setExpanded(true));
    expect(localStorage.getItem('hl-chat-open')).toBe('true');
    expect(localStorage.getItem('hl-chat-expanded')).toBe('true');
  });

  it('clearConversation empties messages and queued interactions', () => {
    localStorage.setItem('hl-chat-pending-interactions', JSON.stringify([
      { component_id: 'x', component: 'spread_payoff', action: 'select_strike', payload: { strike: 1 } },
    ]));
    const { result } = renderHook(() => useChat(), { wrapper });
    expect(result.current.pendingInteractions.length).toBe(1);
    act(() => result.current.clearConversation());
    expect(result.current.messages).toEqual([]);
    expect(result.current.pendingInteractions).toEqual([]);
  });

  it('loads gracefully from corrupt localStorage', () => {
    localStorage.setItem('hl-chat-messages', '{not valid json');
    const { result } = renderHook(() => useChat(), { wrapper });
    expect(result.current.messages).toEqual([]);
  });

  it('queueInteraction dedupes per (instance, action) and removeInteraction drops one', () => {
    const { result } = renderHook(() => useChat(), { wrapper });
    const base = { component_id: 'inst', component: 'spread_payoff', action: 'select_strike' };
    act(() => result.current.queueInteraction({ ...base, payload: { strike: 100 } }));
    act(() => result.current.queueInteraction({ ...base, payload: { strike: 105 } }));
    // Same instance+action -> replaced, not appended.
    expect(result.current.pendingInteractions.length).toBe(1);
    expect(result.current.pendingInteractions[0].payload.strike).toBe(105);
    act(() => result.current.removeInteraction(0));
    expect(result.current.pendingInteractions).toEqual([]);
  });
});
