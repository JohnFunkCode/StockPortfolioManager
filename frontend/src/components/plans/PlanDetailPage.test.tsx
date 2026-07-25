import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import PlanDetailPage from './PlanDetailPage';
import { mockApi, planRow, renderWithProviders, rungRow } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('PlanDetailPage', () => {
  it('renders the plan header and rungs table', async () => {
    mockApi([
      ['/api/plans/5', { plan: planRow({ instance_id: 5, symbol: 'INTC' }), rungs: [rungRow()] }],
      ['/api/symbols/INTC/price', { ticker: 'INTC', price: 118 }],
      [/\/api\//, {}],
    ]);
    renderWithProviders(<PlanDetailPage />, {
      route: '/plans/5',
      path: '/plans/:id',
    });
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
  });

  it('surfaces a load error', async () => {
    mockApi([['/api/plans/9', () => ({ __status: 500, error: 'plan gone' })], [/\/api\//, {}]]);
    renderWithProviders(<PlanDetailPage />, { route: '/plans/9', path: '/plans/:id' });
    await waitFor(() => expect(screen.getByText(/plan gone/i)).toBeInTheDocument());
  });
});
