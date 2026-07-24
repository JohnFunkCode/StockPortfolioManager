import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import EditNotesDialog from './EditNotesDialog';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('EditNotesDialog', () => {
  it('is hidden when closed', () => {
    mockApi([]);
    renderWithProviders(<EditNotesDialog open={false} planId={1} currentNotes="" onClose={() => {}} />);
    expect(screen.queryByLabelText('Notes')).toBeNull();
  });

  it('saves edited notes', async () => {
    const api = mockApi([['/api/plans/5', { instance_id: 5, updated: true }]]);
    const onClose = vi.fn();
    renderWithProviders(<EditNotesDialog open planId={5} currentNotes="old" onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /Save|Update/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.calls.some((c) => c[0].includes('/api/plans/5') && c[1]?.method === 'PATCH')).toBe(true);
  });
});
