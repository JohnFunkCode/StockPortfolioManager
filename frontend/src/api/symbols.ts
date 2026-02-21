import { apiRequest } from './client';
import type { SymbolInfo } from './types';

export const symbolsApi = {
  list: () =>
    apiRequest<{ symbols: SymbolInfo[] }>('/api/symbols'),

  getPrice: (ticker: string) =>
    apiRequest<{ ticker: string; price: number }>(`/api/symbols/${ticker}/price`),
};
