/** Wire + UI types for the chat sidekick. Mirrors api/sse.py's event protocol. */

export interface ChatDirective {
  component: string;
  props: Record<string, unknown>;
}

/** One decoded SSE event from POST /api/chat. */
export type ChatStreamEvent =
  | { type: 'text'; delta: string }
  | {
      type: 'tool_status';
      tool: string;
      args: Record<string, unknown>;
      state: 'running' | 'done' | 'error';
    }
  | { type: 'directive'; directive: ChatDirective }
  | { type: 'error'; message: string }
  | { type: 'done'; stop_reason: string }
  /** Client-side only: a frame whose data payload failed to JSON-parse. */
  | { type: 'parse_error'; raw: string };

/** Ordered content of an assistant message as rendered in the rail. */
export type Segment =
  | { type: 'text'; text: string }
  | { type: 'directive'; directive: ChatDirective }
  | { type: 'tool_status'; tool: string; state: 'running' | 'done' | 'error' };

export interface ChatMessage {
  role: 'user' | 'assistant';
  segments: Segment[];
}

/** History shape sent to the API (assistant segments serialized to text). */
export interface ApiChatMessage {
  role: 'user' | 'assistant';
  content: string;
}
