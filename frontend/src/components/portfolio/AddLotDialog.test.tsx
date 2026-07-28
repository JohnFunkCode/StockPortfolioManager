import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import AddLotDialog from './AddLotDialog';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function createBody(api: ReturnType<typeof mockApi>) {
  const call = api.calls.find(([url, init]) => url.endsWith('/api/portfolio/lots') && init?.method === 'POST');
  return call ? JSON.parse(String(call[1]?.body)) : null;
}

function mount(onClose = vi.fn()) {
  const api = mockApi([
    ['/api/securities/lookup', { symbol: 'AAPL', name: 'Apple Inc.' }],
    ['/api/portfolio/lots', { lot: { lot_id: 5 } }],
  ]);
  renderWithProviders(<AddLotDialog open onClose={onClose} />);
  return { api, onClose };
}

describe('AddLotDialog', () => {
  it('requires a symbol before submitting', () => {
    mount();
    expect(screen.getByRole('button', { name: /Add Lot/i })).toBeDisabled();
  });

  it('looks up the symbol on blur and fills in the name', async () => {
    mount();
    const symbolField = screen.getByLabelText(/Symbol/i);
    fireEvent.change(symbolField, { target: { value: 'aapl' } });
    fireEvent.blur(symbolField);

    await waitFor(() => expect((screen.getByLabelText(/^Name/) as HTMLInputElement).value).toBe('Apple Inc.'));
    expect((symbolField as HTMLInputElement).value).toBe('AAPL');
  });

  it('validates required fields before submitting', () => {
    mount();
    const symbolField = screen.getByLabelText(/Symbol/i);
    fireEvent.change(symbolField, { target: { value: 'AAPL' } });
    fireEvent.click(screen.getByRole('button', { name: /Add Lot/i }));
    expect(screen.getByText('Purchase price, quantity, and trade date are required.')).toBeInTheDocument();
  });

  it('submits a new lot', async () => {
    const { api, onClose } = mount();
    fireEvent.change(screen.getByLabelText(/Symbol/i), { target: { value: 'AAPL' } });
    fireEvent.change(screen.getByLabelText(/Purchase Price/i), { target: { value: '150' } });
    fireEvent.change(screen.getByLabelText(/Quantity/i), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText(/Trade Date/i), { target: { value: '2026-01-15' } });
    fireEvent.click(screen.getByRole('button', { name: /Add Lot/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(createBody(api)).toEqual({
      symbol: 'AAPL',
      purchase_price: 150,
      quantity: 5,
      trade_date: '2026-01-15',
    });
  });

  it('shows an error and stays open when creation fails', async () => {
    const onClose = vi.fn();
    mockApi([
      ['/api/securities/lookup', { symbol: 'AAPL', name: 'Apple Inc.' }],
      ['/api/portfolio/lots', () => ({ __status: 500, error: 'duplicate lot' })],
    ]);
    renderWithProviders(<AddLotDialog open onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/Symbol/i), { target: { value: 'AAPL' } });
    fireEvent.change(screen.getByLabelText(/Purchase Price/i), { target: { value: '150' } });
    fireEvent.change(screen.getByLabelText(/Quantity/i), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText(/Trade Date/i), { target: { value: '2026-01-15' } });
    fireEvent.click(screen.getByRole('button', { name: /Add Lot/i }));

    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('cancel closes without submitting', () => {
    const { api, onClose } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(api.calls).toHaveLength(0);
  });
});
