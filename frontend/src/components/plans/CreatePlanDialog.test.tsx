import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import CreatePlanDialog from './CreatePlanDialog';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('CreatePlanDialog', () => {
  it('is hidden when closed', () => {
    mockApi([]);
    renderWithProviders(<CreatePlanDialog open={false} onClose={() => {}} onCreated={() => {}} />);
    expect(screen.queryByText(/Create.*Plan/i)).toBeNull();
  });

  it('submits a new plan', async () => {
    const api = mockApi([['/api/plans', { instance_id: 3, symbol: 'INTC' }]]);
    const onCreated = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={onCreated} />);
    const symbol = document.querySelectorAll('input[type="text"]')[0] as HTMLInputElement;
    fireEvent.change(symbol, { target: { value: 'INTC' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));
    await waitFor(() =>
      expect(api.calls.some((c) => c[0].includes('/api/plans') && c[1]?.method === 'POST')).toBe(true),
    );
  });
});
