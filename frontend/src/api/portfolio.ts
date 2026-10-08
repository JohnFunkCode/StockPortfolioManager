import { apiRequest } from './client';
import type {
  CloseLotPayload,
  CloseLotResponse,
  ClosePreviewResponse,
  CreateLotPayload,
  CreateLotResponse,
  DeleteLotResponse,
  Lot,
  PortfolioTotals,
  Sale,
  SymbolRow,
  UpdateLotPayload,
  UpdateLotResponse,
  UpdateSalePayload,
  UpdateSaleResponse,
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

  /** Which lots a sale of `shares` would touch; writes nothing. */
  previewClose: (lotId: number, shares: number) =>
    apiRequest<ClosePreviewResponse>(`/api/portfolio/lots/${lotId}/close/preview`, {
      method: 'POST',
      body: JSON.stringify({ shares }),
    }),

  getSales: (symbol?: string) =>
    apiRequest<{ sales: Sale[] }>(
      `/api/portfolio/sales${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`
    ),

  updateSale: (saleId: number, data: UpdateSalePayload) =>
    apiRequest<UpdateSaleResponse>(`/api/portfolio/sales/${saleId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
};
