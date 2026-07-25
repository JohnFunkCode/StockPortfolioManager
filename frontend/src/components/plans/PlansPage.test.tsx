import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import PlansPage from './PlansPage';
import { mockApi, planRow, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('PlansPage', () => {
  it('lists plans from the active query', async () => {
    mockApi([['/api/plans', { plans: [planRow({ symbol: 'INTC' }), planRow({ instance_id: 2, symbol: 'WMT' })] }]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.getByText('WMT')).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockApi([['/api/plans', () => ({ __status: 500, error: 'plans down' })]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText(/plans down/i)).toBeInTheDocument());
  });
});
