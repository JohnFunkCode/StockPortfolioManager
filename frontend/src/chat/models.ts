/**
 * Sidekick chat model catalog (issue #124) — a **fallback** copy only.
 *
 * The authoritative catalog is the `models` array on the `GET /api/settings`
 * response (see `frontend/src/api/settings.ts`); this list renders labels
 * before that fetch resolves, or if it fails. Frontend is a separate
 * deployable from the backend `quantcore.chat_models` module, so it owns
 * this copy rather than importing across the boundary.
 */
export interface ChatModel {
  id: string;
  name: string;
  description: string;
}

export const CHAT_MODELS: ChatModel[] = [
  {
    id: 'claude-sonnet-5',
    name: 'Claude Sonnet 5',
    description: 'Recommended daily driver — balances speed and capability.',
  },
  {
    id: 'claude-opus-4-8',
    name: 'Claude Opus 4.8',
    description: 'Heavy-lifting flagship for complex tasks and deep reasoning.',
  },
  {
    id: 'claude-fable-5',
    name: 'Claude Fable 5',
    description: 'Most capable general model; best for long-horizon, multi-file agentic work.',
  },
];

export const DEFAULT_CHAT_MODEL = 'claude-sonnet-5';
