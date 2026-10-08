import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import CloseLotDialog from './CloseLotDialog';
import { lotRow, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function closeBody(api: ReturnType<typeof mockApi>) {
  const call = api.calls.find(
    ([url, init]) => url.includes('/close') && !url.includes('/preview') && init?.method === 'POST',
  );
  return call ? JSON.parse(String(call[1]?.body)) : null;
}

const ONE_LOT = [{ lot_id: 1, shares: 10, trade_date: '2026-01-02', purchase_price: 30 }];
const TWO_LOTS = [
  { lot_id: 1, shares: 6, trade_date: '2026-01-02', purchase_price: 30 },
  { lot_id: 2, shares: 4, trade_date: '2026-03-10', purchase_price: 41 },
];

// The preview route must come first: matching is by substring, in order.
function mount(onClose = vi.fn(), allocations: object[] = ONE_LOT) {
  const api = mockApi([
    ['/api/portfolio/lots/1/close/preview', { symbol: 'INTC', allocations }],
    ['/api/portfolio/lots/1/close', { lot: lotRow({ status: 'CLOSED' }) }],
  ]);
  renderWithProviders(<CloseLotDialog open lot={lotRow()} onClose={onClose} />);
  return { api, onClose };
}

describe('CloseLotDialog', () => {
  it('prefills shares and price from the lot', () => {
    mount();
    const nums = document.querySelectorAll('input[type="number"]') as NodeListOf<HTMLInputElement>;
    expect(nums[0].value).toBe('10');
    expect(nums[1].value).toBe('35');
  });

  it('submits a sale', async () => {
    const { api } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    expect(closeBody(api)).toMatchObject({ shares: 10, sale_price: 35 });
  });

  it('closes once the sale succeeds', async () => {
    const { onClose } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('rejects a zero share count', () => {
    mount();
    const nums = document.querySelectorAll('input[type="number"]') as NodeListOf<HTMLInputElement>;
    fireEvent.change(nums[0], { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    expect(screen.getByText('Shares must be greater than zero.')).toBeInTheDocument();
  });

  it('rejects a zero sale price', () => {
    mount();
    const nums = document.querySelectorAll('input[type="number"]') as NodeListOf<HTMLInputElement>;
    fireEvent.change(nums[1], { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    expect(screen.getByText('Sale price must be greater than zero.')).toBeInTheDocument();
  });

  it('cancel closes without submitting', () => {
    const { api, onClose } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(closeBody(api)).toBeNull();
  });

  it('sends the reason for sale when one is entered', async () => {
    const { api } = mount();
    fireEvent.change(screen.getByLabelText(/Reason for sale/i), { target: { value: ' Hit my target ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    const body = closeBody(api);
    expect(body).toMatchObject({ shares: 10, sale_price: 35, notes: 'Hit my target' });
    expect(body).not.toHaveProperty('lot_notes');
  });

  it('sends neither notes nor lot_notes when the reason is blank', async () => {
    const { api } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    expect(closeBody(api)).not.toHaveProperty('notes');
    expect(closeBody(api)).not.toHaveProperty('lot_notes');
  });

  it('offers one shared note when a sale spans several lots', async () => {
    const { api } = mount(vi.fn(), TWO_LOTS);
    const checkbox = await screen.findByLabelText('Same note for all lots');
    expect(checkbox).toBeChecked();
    expect(screen.getByText('This sale draws from 2 lots.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Reason for sale/i), { target: { value: 'Rebalancing' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    const body = closeBody(api);
    expect(body).toMatchObject({ notes: 'Rebalancing' });
    expect(body).not.toHaveProperty('lot_notes');
  });

  it('sends only the filled per-lot notes when "same note" is turned off', async () => {
    const { api } = mount(vi.fn(), TWO_LOTS);
    fireEvent.click(await screen.findByLabelText('Same note for all lots'));

    expect(screen.queryByLabelText(/Reason for sale/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Lot #1 /)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Lot #2 /), { target: { value: 'Newer lot ran too far' } });

    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    const body = closeBody(api);
    expect(body.lot_notes).toEqual({ '2': 'Newer lot ran too far' });
    expect(body).not.toHaveProperty('notes');
  });

  it('asks the preview endpoint which lots the share count touches', async () => {
    const { api } = mount();
    await waitFor(() => {
      const call = api.calls.find(([url]) => url.includes('/close/preview'));
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ shares: 10 });
    });
  });
});
