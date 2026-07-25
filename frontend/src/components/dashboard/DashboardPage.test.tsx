import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import DashboardPage from './DashboardPage';
import { mockApi, planRow, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('DashboardPage', () => {
  it('renders stat cards and the active plans section', async () => {
    mockApi([
      ['/api/dashboard/stats', { active_plans: 3, total_symbols: 12, superseded_plans: 1 }],
      ['/api/plans', { plans: [planRow({ symbol: 'INTC' })] }],
      ['/api/portfolio/delta-exposure', { portfolio_net_daoi: 0, positions: [] }],
      [/\/api\//, {}],
    ]);
    renderWithProviders(<DashboardPage />);
    await waitFor(() => expect(screen.getAllByText('Active Plans').length).toBeGreaterThan(0));
    expect(screen.getByText('3')).toBeInTheDocument();
  });
});
