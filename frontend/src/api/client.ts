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
    throw new ApiError(error.message || error.error || response.statusText, response.status);
  }

  return response.json();
}
