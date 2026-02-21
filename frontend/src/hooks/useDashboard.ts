import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '../api/dashboard';

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: dashboardApi.getStats,
    refetchInterval: 60000,
  });
}
