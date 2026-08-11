import { apiRequest } from './client';

/**
 * `GET /api/symbols` is deliberately absent here. The list route still exists
 * and still carries `active_plan_id` for MCP callers, but no page reads it:
 * `/symbols` was a Harvester registry promoted to top-level nav, duplicating
 * `/securities` three columns out of four and `/plans` the fourth (issue #147
 * Part G1). The per-ticker price below backs `LivePrice`, which stays.
 */
export const symbolsApi = {
  getPrice: (ticker: string) =>
    apiRequest<{ ticker: string; price: number }>(`/api/symbols/${ticker}/price`),
};
