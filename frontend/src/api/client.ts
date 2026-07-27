const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
// Optional bearer token for talking to a JWT-enforced API (e.g. the prod Cloud Run
// deployment). Left unset for local/compose, which run with AUTH_DISABLED.
const API_TOKEN = import.meta.env.VITE_API_TOKEN || '';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type NotProvisionedListener = () => void;
const notProvisionedListeners = new Set<NotProvisionedListener>();

/** Subscribe to the global "principal isn't provisioned" signal (issue #126
 * decision #2/#4): fired whenever any apiRequest call comes back as a 403
 * whose body resolves to "not_provisioned". Returns an unsubscribe function. */
export function onNotProvisioned(listener: NotProvisionedListener): () => void {
  notProvisionedListeners.add(listener);
  return () => notProvisionedListeners.delete(listener);
}

export async function apiRequest<T>(
  endpoint: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    // `detail` is FastAPI's error field; `message`/`error` are legacy shapes.
    const message = error.message || error.error || error.detail || response.statusText;
    if (response.status === 403 && message === 'not_provisioned') {
      notProvisionedListeners.forEach((listener) => listener());
    }
    throw new ApiError(message, response.status);
  }

  return response.json();
}
