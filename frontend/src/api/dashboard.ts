import { apiRequest } from './client';
import type { DashboardStats } from './types';

export const dashboardApi = {
  getStats: () => apiRequest<DashboardStats>('/api/dashboard/stats'),
};
