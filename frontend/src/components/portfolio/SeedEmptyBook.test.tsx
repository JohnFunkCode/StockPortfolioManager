import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import SeedEmptyBook from './SeedEmptyBook';
import { mockApi, order, ordersResponse, renderWithProviders, seedResult } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function mount(orders: object, seed: object = seedResult()) {
  const api = mockApi([
    ['/api/portfolio/orders/seed', seed],
    ['/api/portfolio/orders', orders],
  ]);
  renderWithProviders(<SeedEmptyBook bookId={7} />);
  return api;
}

describe('SeedEmptyBook', () => {
  it('offers nothing while the orders load, or for a book that has had an order', async () => {
    const api = mount(ordersResponse([order()]));
    expect(screen.queryByRole('button')).toBeNull();
    await waitFor(() => expect(api.calls.length).toBe(1));
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('copies the real holdings into a book with no orders and says what happened', async () => {
    const api = mount(
      ordersResponse([]),
      seedResult({ filled: 2, pending: 1, rejected: 1, skipped: ['BRK.B'] }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Copy my real holdings' }));

    expect(await screen.findByText('Copied 2 holdings at the current price.')).toBeInTheDocument();
    expect(screen.getByText(/1 holding will be copied at the next session's opening price/)).toBeInTheDocument();
    expect(screen.getByText(/1 holding could not be priced and was left out/)).toBeInTheDocument();
    expect(screen.getByText('Left out: BRK.B.')).toBeInTheDocument();
    const post = api.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe('/api/portfolio/orders/seed');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ book_id: 7 });
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows why the copy failed and keeps the button', async () => {
    mount(ordersResponse([]), { __status: 422, error: 'the real book holds nothing to copy' });
    fireEvent.click(await screen.findByRole('button', { name: 'Copy my real holdings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('the real book holds nothing to copy');
    expect(screen.getByRole('button', { name: 'Copy my real holdings' })).toBeEnabled();
  });
});
