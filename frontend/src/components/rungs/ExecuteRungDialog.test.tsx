import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import ExecuteRungDialog from './ExecuteRungDialog';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

describe('ExecuteRungDialog', () => {
  it('submits an execution', async () => {
    const api = mockApi([['/api/rungs/4/execute', { rung_id: 4, status: 'EXECUTED' }]]);
    const onClose = vi.fn();
    renderWithProviders(
      <ExecuteRungDialog open rungId={4} sharesSoldPlanned={10} targetPrice={120} onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Execute|Confirm|Record/i }));
    await waitFor(() =>
      expect(api.calls.some((c) => c[0].includes('/execute') && c[1]?.method === 'POST')).toBe(true),
    );
  });
});
