import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import AchieveRungDialog from './AchieveRungDialog';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('AchieveRungDialog', () => {
  it('is hidden when closed', () => {
    mockApi([]);
    renderWithProviders(<AchieveRungDialog open={false} rungId={1} targetPrice={120} onClose={() => {}} />);
    expect(screen.queryByText('Mark Rung Achieved')).toBeNull();
  });

  it('submits the achievement and closes', async () => {
    const api = mockApi([['/api/rungs/3/achieve', { rung_id: 3, status: 'ACHIEVED' }]]);
    const onClose = vi.fn();
    renderWithProviders(<AchieveRungDialog open rungId={3} targetPrice={120} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /Mark|Achieve|Confirm/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.calls.some((c) => c[0].includes('/achieve') && c[1]?.method === 'POST')).toBe(true);
  });
});
