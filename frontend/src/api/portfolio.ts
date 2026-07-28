import { apiRequest } from './client';
import type {
  CloseLotPayload,
  CloseLotResponse,
  CreateLotPayload,
  CreateLotResponse,
  DeleteLotResponse,
  Lot,
  PortfolioTotals,
  SymbolRow,
  UpdateLotPayload,
  UpdateLotResponse,
} from './portfolioTypes';

export const portfolioApi = {
  getLots: (force = false) =>
    apiRequest<{ lots: Lot[] }>(`/api/portfolio/lots${force ? '?force=true' : ''}`),

  getSymbolRows: (force = false) =>
    apiRequest<{ symbols: SymbolRow[]; totals: PortfolioTotals }>(
      `/api/portfolio/symbols${force ? '?force=true' : ''}`
    ),

  createLot: (data: CreateLotPayload) =>
    apiRequest<CreateLotResponse>('/api/portfolio/lots', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateLot: (lotId: number, data: UpdateLotPayload) =>
    apiRequest<UpdateLotResponse>(`/api/portfolio/lots/${lotId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  deleteLot: (lotId: number) =>
    apiRequest<DeleteLotResponse>(`/api/portfolio/lots/${lotId}`, {
      method: 'DELETE',
    }),

  closeLot: (lotId: number, data: CloseLotPayload) =>
    apiRequest<CloseLotResponse>(`/api/portfolio/lots/${lotId}/close`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};
