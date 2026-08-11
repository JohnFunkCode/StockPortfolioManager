import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import PlansPage from './PlansPage';
import { mockApi, planRow, renderWithProviders } from '../../testUtils';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

afterEach(() => {
  vi.unstubAllGlobals();
  mockNavigate.mockClear();
});

/** Two plans: one ACTIVE (archivable), one SUPERSEDED (not). */
function twoPlans() {
  return {
    plans: [
      planRow({ symbol: 'INTC', price_asof: 118.25, h_threshold: 0.12 }),
      planRow({ instance_id: 2, symbol: 'WMT', status: 'SUPERSEDED', price_asof: 95.5 }),
    ],
  };
}

describe('PlansPage', () => {
  it('lists plans from the active query', async () => {
    mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.getByText('WMT')).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockApi([['/api/plans', () => ({ __status: 500, error: 'plans down' })]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText(/plans down/i)).toBeInTheDocument());
  });

  it('formats the grid cells rather than dumping raw values', async () => {
    mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    // valueFormatters: currency, percent, date.
    expect(screen.getByText('$118.25')).toBeInTheDocument();
    expect(screen.getByText('12.00%')).toBeInTheDocument();
    expect(screen.queryByText('2026-07-01T00:00:00Z')).toBeNull();
  });

  it('navigates to the plan detail page on row click', async () => {
    mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByText('INTC'));
    expect(mockNavigate).toHaveBeenCalledWith('/plans/1');
  });

  it('refetches with the selected status filter', async () => {
    const api = mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Superseded' }));
    await waitFor(() =>
      expect(api.calls.some(([url]) => url.includes('status=SUPERSEDED'))).toBe(true),
    );
  });

  it('offers the CLOSED filter and refetches with it', async () => {
    const api = mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Closed' }));
    await waitFor(() =>
      expect(api.calls.some(([url]) => url.includes('status=CLOSED'))).toBe(true),
    );
  });

  it('opens the create dialog from the New Plan button', async () => {
    mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /New Plan/i }));
    expect(await screen.findByText(/Create Harvest Plan/i)).toBeInTheDocument();
  });

  it('opens the notes editor for a row without navigating', async () => {
    mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('button', { name: /Notes/i })[0]);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    // The row-click handler must not also fire (stopPropagation).
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('offers Archive only for ACTIVE plans', async () => {
    mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    // Two rows, but only the ACTIVE one gets an Archive button.
    expect(screen.getAllByRole('button', { name: /Notes/i })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: /Archive/i })).toHaveLength(1);
  });

  it('archives a plan through the confirm dialog', async () => {
    const api = mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Archive/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Archive/i }));

    await waitFor(() =>
      expect(
        api.calls.some(([url, init]) => url.includes('/api/plans/1') && init?.method === 'DELETE'),
      ).toBe(true),
    );
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('cancelling the archive dialog sends no request', async () => {
    const api = mockApi([['/api/plans', twoPlans()]]);
    renderWithProviders(<PlansPage />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Archive/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Cancel/i }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  });
});
