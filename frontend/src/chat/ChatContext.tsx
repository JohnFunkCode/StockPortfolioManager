/**
 * Chat sidekick state: conversation, streaming reducer, rail visibility.
 * History persists in localStorage (lazy init + try/catch, matching the
 * hl-theme / securities-tag-filter convention). The backend is stateless —
 * the full serialized history is sent on every turn.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { streamChat } from '../api/chatStream';
import type {
  ApiChatMessage,
  ChatInteraction,
  ChatMessage,
  ChatStreamEvent,
  Segment,
} from './types';

const MESSAGES_KEY = 'hl-chat-messages';
const OPEN_KEY = 'hl-chat-open';
const EXPANDED_KEY = 'hl-chat-expanded';
const PENDING_KEY = 'hl-chat-pending-interactions';
const CONSUMED_KEY = 'hl-chat-consumed-interactions';

/** Assistant segments -> the plain-text turn the model sees next time. */
export function serializeForApi(messages: ChatMessage[]): ApiChatMessage[] {
  const out: ApiChatMessage[] = [];
  for (const message of messages) {
    const parts: string[] = [];
    for (const segment of message.segments) {
      if (segment.type === 'text' && segment.text.trim()) {
        parts.push(segment.text);
      } else if (segment.type === 'directive') {
        const ticker = String(segment.directive.props?.ticker ?? '');
        parts.push(`[shown: ${segment.directive.component} ${ticker}]`);
      }
      // tool_status segments are UI affordances only — not history.
    }
    const content = parts.join('\n').trim();
    if (content) out.push({ role: message.role, content });
  }
  return out;
}

function applyEvent(segments: Segment[], event: ChatStreamEvent): Segment[] {
  switch (event.type) {
    case 'text': {
      const last = segments[segments.length - 1];
      if (last?.type === 'text') {
        return [...segments.slice(0, -1), { type: 'text', text: last.text + event.delta }];
      }
      return [...segments, { type: 'text', text: event.delta }];
    }
    case 'directive':
      return [...segments, { type: 'directive', directive: event.directive }];
    case 'tool_status': {
      if (event.state !== 'running') {
        // Update the matching running chip in place (last one wins).
        let idx = -1;
        for (let i = segments.length - 1; i >= 0; i--) {
          const s = segments[i];
          if (s.type === 'tool_status' && s.tool === event.tool && s.state === 'running') {
            idx = i;
            break;
          }
        }
        if (idx >= 0) {
          const updated = [...segments];
          updated[idx] = { type: 'tool_status', tool: event.tool, state: event.state };
          return updated;
        }
      }
      return [...segments, { type: 'tool_status', tool: event.tool, state: event.state }];
    }
    default:
      return segments;
  }
}

interface ChatContextValue {
  messages: ChatMessage[];
  isStreaming: boolean;
  error: string | null;
  railOpen: boolean;
  setRailOpen: (open: boolean) => void;
  expanded: boolean;
  setExpanded: (expanded: boolean) => void;
  sendMessage: (text: string, extraInteractions?: ChatInteraction[]) => Promise<void>;
  clearConversation: () => void;
  /** Context-mode gestures waiting to ride along with the next message. */
  pendingInteractions: ChatInteraction[];
  queueInteraction: (interaction: ChatInteraction) => void;
  removeInteraction: (index: number) => void;
  /**
   * Gestures already sent to the model, keyed by component_id. Once an
   * instance appears here it is part of the conversational record — the
   * rendered card locks and its mark becomes immutable.
   */
  consumedInteractions: Record<string, ChatInteraction[]>;
}

const ChatContext = createContext<ChatContextValue | null>(null);

function loadStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    loadStored<ChatMessage[]>(MESSAGES_KEY, []),
  );
  const [railOpen, setRailOpenState] = useState<boolean>(() =>
    loadStored<boolean>(OPEN_KEY, false),
  );
  const [expanded, setExpandedState] = useState<boolean>(() =>
    loadStored<boolean>(EXPANDED_KEY, false),
  );
  const [pendingInteractions, setPendingInteractions] = useState<ChatInteraction[]>(() =>
    loadStored<ChatInteraction[]>(PENDING_KEY, []),
  );
  const [consumedInteractions, setConsumedInteractions] = useState<
    Record<string, ChatInteraction[]>
  >(() => loadStored<Record<string, ChatInteraction[]>>(CONSUMED_KEY, {}));
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(MESSAGES_KEY, JSON.stringify(messages));
    } catch {
      /* storage full/unavailable — history simply won't persist */
    }
  }, [messages]);

  useEffect(() => {
    try {
      localStorage.setItem(PENDING_KEY, JSON.stringify(pendingInteractions));
    } catch {
      /* ignore */
    }
  }, [pendingInteractions]);

  useEffect(() => {
    try {
      localStorage.setItem(CONSUMED_KEY, JSON.stringify(consumedInteractions));
    } catch {
      /* ignore */
    }
  }, [consumedInteractions]);

  const queueInteraction = useCallback((interaction: ChatInteraction) => {
    setPendingInteractions((prev) => {
      // One pending gesture per (instance, action): re-clicking replaces.
      const rest = prev.filter(
        (p) =>
          !(p.component_id === interaction.component_id && p.action === interaction.action),
      );
      return [...rest, interaction];
    });
  }, []);

  const removeInteraction = useCallback((index: number) => {
    setPendingInteractions((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const setRailOpen = useCallback((open: boolean) => {
    setRailOpenState(open);
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify(open));
    } catch {
      /* ignore */
    }
  }, []);

  const setExpanded = useCallback((next: boolean) => {
    setExpandedState(next);
    try {
      localStorage.setItem(EXPANDED_KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  }, []);

  const clearConversation = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setPendingInteractions([]);
    setConsumedInteractions({});
    setError(null);
    setIsStreaming(false);
  }, []);

  const sendMessage = useCallback(
    async (text: string, extraInteractions?: ChatInteraction[]) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;
      setError(null);
      // Attach queued context-mode gestures plus any message-mode gesture,
      // then clear the queue — interactions are one-shot context.
      const interactions = [...pendingInteractions, ...(extraInteractions ?? [])];
      setPendingInteractions([]);
      // Sent gestures become part of the record: lock their card instances.
      if (interactions.length > 0) {
        setConsumedInteractions((prev) => {
          const next = { ...prev };
          for (const interaction of interactions) {
            next[interaction.component_id] = [
              ...(next[interaction.component_id] ?? []),
              interaction,
            ];
          }
          return next;
        });
      }

      const userMessage: ChatMessage = {
        role: 'user',
        segments: [{ type: 'text', text: trimmed }],
      };
      // History for the API = everything up to and including this user turn.
      const history = serializeForApi([...messages, userMessage]);

      setMessages((prev) => [...prev, userMessage, { role: 'assistant', segments: [] }]);
      setIsStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const applyToAssistant = (event: ChatStreamEvent) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role !== 'assistant') return prev;
          next[next.length - 1] = { ...last, segments: applyEvent(last.segments, event) };
          return next;
        });
      };

      // If the stream dies without a terminal frame (server restart, network
      // drop), tool chips stuck in 'running' would spin forever — flip any
      // survivors to 'error' once the stream is over, however it ended.
      const finalizeInterruptedTools = () => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role !== 'assistant') return prev;
          let changed = false;
          const segments = last.segments.map((segment) => {
            if (segment.type === 'tool_status' && segment.state === 'running') {
              changed = true;
              return { ...segment, state: 'error' as const };
            }
            return segment;
          });
          if (!changed) return prev;
          next[next.length - 1] = { ...last, segments };
          return next;
        });
      };

      try {
        await streamChat(
          history,
          (event) => {
            if (event.type === 'error') {
              setError(event.message);
            } else if (event.type === 'parse_error') {
              setError('Received an unreadable chunk from the server.');
            } else if (event.type !== 'done') {
              applyToAssistant(event);
            }
          },
          controller.signal,
          interactions,
        );
      } catch (exc) {
        if ((exc as Error).name !== 'AbortError') {
          setError((exc as Error).message || 'Chat request failed.');
        }
      } finally {
        finalizeInterruptedTools();
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [messages, isStreaming, pendingInteractions],
  );

  return (
    <ChatContext.Provider
      value={{
        messages,
        isStreaming,
        error,
        railOpen,
        setRailOpen,
        expanded,
        setExpanded,
        sendMessage,
        clearConversation,
        pendingInteractions,
        queueInteraction,
        removeInteraction,
        consumedInteractions,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChat(): ChatContextValue {
  const value = useContext(ChatContext);
  if (!value) throw new Error('useChat must be used inside <ChatProvider>');
  return value;
}

/** Like useChat, but safe outside <ChatProvider> — returns null instead of
 * throwing, so directive components stay renderable in isolation/tests. */
export function useChatOptional(): ChatContextValue | null {
  return useContext(ChatContext);
}
