import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import CloseLotDialog from './CloseLotDialog';
import { lotRow, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function closeBody(api: ReturnType<typeof mockApi>) {
  const call = api.calls.find(([url, init]) => url.includes('/close') && init?.method === 'POST');
  return call ? JSON.parse(String(call[1]?.body)) : null;
}

function mount(onClose = vi.fn()) {
  const api = mockApi([['/api/portfolio/lots/1/close', { lot: lotRow({ status: 'CLOSED' }) }]]);
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
    expect(api.calls).toHaveLength(0);
  });
});
