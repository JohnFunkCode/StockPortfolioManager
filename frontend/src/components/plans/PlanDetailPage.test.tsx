import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import PlanDetailPage from './PlanDetailPage';
import { mockApi, planRow, renderWithProviders, rungRow } from '../../testUtils';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

afterEach(() => {
  vi.unstubAllGlobals();
  mockNavigate.mockClear();
});

const FULL_PLAN = planRow({
  instance_id: 5,
  symbol: 'INTC',
  h_threshold: 0.12,
  annual_vol: 0.34,
  r_daily: 0.000512,
  v0_floor: 10_000,
  price_asof: 118.25,
  history_end_date: '2026-06-30T00:00:00Z',
  notes: 'ladder tuned after Q2',
});

function mountPlan(overrides: object = {}, rungs = [rungRow()]) {
  const api = mockApi([
    ['/api/plans/5', { plan: { ...FULL_PLAN, ...overrides }, rungs }],
    ['/api/symbols/INTC/price', { ticker: 'INTC', price: 118 }],
    [/\/api\//, {}],
  ]);
  renderWithProviders(<PlanDetailPage />, { route: '/plans/5', path: '/plans/:id' });
  return api;
}

describe('PlanDetailPage', () => {
  it('renders the plan header and rungs table', async () => {
    mountPlan();
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.getByText('Rungs')).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockApi([['/api/plans/9', () => ({ __status: 500, error: 'plan gone' })], [/\/api\//, {}]]);
    renderWithProviders(<PlanDetailPage />, { route: '/plans/9', path: '/plans/:id' });
    await waitFor(() => expect(screen.getByText(/plan gone/i)).toBeInTheDocument());
  });

  it('formats every summary field', async () => {
    mountPlan();
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.getByText('12.00%')).toBeInTheDocument();   // h_threshold
    expect(screen.getByText('34.00%')).toBeInTheDocument();   // annual_vol
    expect(screen.getByText('0.0512%')).toBeInTheDocument();  // r_daily
    expect(screen.getByText('$10,000.00')).toBeInTheDocument(); // v0_floor
    expect(screen.getByText('$118.25')).toBeInTheDocument();  // price_asof
  });

  it('shows notes only when the plan has them', async () => {
    mountPlan();
    await waitFor(() => expect(screen.getByText('ladder tuned after Q2')).toBeInTheDocument());
  });

  it('omits the notes block when there are none', async () => {
    mountPlan({ notes: null });
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.queryByText('ladder tuned after Q2')).toBeNull();
  });

  it('navigates back to the plan list', async () => {
    mountPlan();
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Back to Plans/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/plans');
  });

  it('opens the notes editor', async () => {
    mountPlan();
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Notes/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('hides Archive for a plan that is not ACTIVE', async () => {
    mountPlan({ status: 'SUPERSEDED' });
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Archive/i })).toBeNull();
  });

  it('archives the plan and returns to the list', async () => {
    const api = mountPlan();
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Archive/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Archive the INTC plan/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /Archive/i }));

    await waitFor(() =>
      expect(
        api.calls.some(([url, init]) => url.includes('/api/plans/5') && init?.method === 'DELETE'),
      ).toBe(true),
    );
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/plans'));
  });

  it('cancelling the archive dialog sends no request', async () => {
    const api = mountPlan();
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Archive/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Cancel/i }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
