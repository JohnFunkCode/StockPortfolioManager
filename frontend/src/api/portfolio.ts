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

/** The query string for a portfolio read. No `bookId` means the real book
 * (#431): the parameter is left off, never sent as the real book's id. */
function readQuery(params: { force?: boolean; symbol?: string; bookId?: number | null }): string {
  const parts: string[] = [];
  if (params.force) parts.push('force=true');
  if (params.symbol) parts.push(`symbol=${encodeURIComponent(params.symbol)}`);
  if (params.bookId != null) parts.push(`book_id=${params.bookId}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

export const portfolioApi = {
  getLots: (force = false, bookId?: number | null) =>
    apiRequest<{ lots: Lot[] }>(`/api/portfolio/lots${readQuery({ force, bookId })}`),

  getSymbolRows: (force = false, bookId?: number | null) =>
    apiRequest<{ symbols: SymbolRow[]; totals: PortfolioTotals }>(
      `/api/portfolio/symbols${readQuery({ force, bookId })}`
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

  getSales: (symbol?: string, bookId?: number | null) =>
    apiRequest<{ sales: Sale[] }>(`/api/portfolio/sales${readQuery({ symbol, bookId })}`),

  updateSale: (saleId: number, data: UpdateSalePayload) =>
    apiRequest<UpdateSaleResponse>(`/api/portfolio/sales/${saleId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
};
