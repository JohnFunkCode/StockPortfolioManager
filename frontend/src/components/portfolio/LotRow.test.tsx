import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Table, TableBody } from '@mui/material';
import LotRow from './LotRow';
import { lotRow, mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function mount(overrides: object = {}) {
  const api = mockApi([
    [/\/api\/portfolio\/lots\/1$/, { lot: lotRow({ purchase_price: 32, quantity: 12 }) }],
    ['/api/portfolio/symbols', { symbols: [], totals: null }],
  ]);
  renderWithProviders(
    <Table>
      <TableBody>
        <LotRow lot={lotRow(overrides)} />
      </TableBody>
    </Table>,
  );
  return { api };
}

describe('LotRow', () => {
  it('renders lot values', () => {
    mount();
    expect(screen.getByText('$30.00')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByText('16.67%')).toBeInTheDocument();
  });

  it('shows — for days held / dollars per day when null', () => {
    mount({ days_held: null, dollars_per_day: null });
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1);
  });

  it('edits and saves purchase price and quantity', async () => {
    const { api } = mount();
    fireEvent.click(screen.getByLabelText('Edit lot'));

    const numberInputs = document.querySelectorAll('input[type="number"]') as NodeListOf<HTMLInputElement>;
    fireEvent.change(numberInputs[0], { target: { value: '32' } });
    fireEvent.change(numberInputs[1], { target: { value: '12' } });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => {
      const call = api.calls.find(([url, init]) => url.includes('/api/portfolio/lots/1') && init?.method === 'PATCH');
      expect(call).toBeTruthy();
    });
    const call = api.calls.find(([url, init]) => url.includes('/api/portfolio/lots/1') && init?.method === 'PATCH');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ purchase_price: 32, quantity: 12 });
  });

  it('cancel exits edit mode without submitting', () => {
    const { api } = mount();
    fireEvent.click(screen.getByLabelText('Edit lot'));
    fireEvent.click(screen.getByLabelText('Cancel'));
    expect(screen.getByLabelText('Edit lot')).toBeInTheDocument();
    expect(api.calls).toHaveLength(0);
  });

  it('opens the sell dialog', () => {
    mount();
    fireEvent.click(screen.getByLabelText('Sell / close lot'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Sell INTC')).toBeInTheDocument();
  });

  it('flags a lot that has a purchase note', () => {
    mount({ notes: 'Breakout above the 200-day' });
    expect(screen.getByRole('img', { name: 'Lot note' })).toBeInTheDocument();
  });

  it('shows no note flag when the lot has no note', () => {
    mount({ notes: null });
    expect(screen.queryByRole('img', { name: 'Lot note' })).not.toBeInTheDocument();
  });

  it('edits the purchase note through PATCH', async () => {
    const { api } = mount({ notes: 'old reason' });
    fireEvent.click(screen.getByLabelText('Edit note'));
    const field = screen.getByRole('textbox', { name: /Reason for purchase/i }) as HTMLTextAreaElement;
    expect(field.value).toBe('old reason');
    fireEvent.change(field, { target: { value: ' new reason ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const call = api.calls.find(([url, init]) => url.includes('/api/portfolio/lots/1') && init?.method === 'PATCH');
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ notes: 'new reason' });
    });
  });

  it('clears the purchase note by saving an empty one', async () => {
    const { api } = mount({ notes: 'old reason' });
    fireEvent.click(screen.getByLabelText('Edit note'));
    fireEvent.change(screen.getByRole('textbox', { name: /Reason for purchase/i }), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const call = api.calls.find(([url, init]) => url.includes('/api/portfolio/lots/1') && init?.method === 'PATCH');
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ notes: '' });
    });
  });

  it('deletes a lot after confirmation', async () => {
    const { api } = mount();
    fireEvent.click(screen.getByLabelText('Delete lot'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      const call = api.calls.find(([url, init]) => url.includes('/api/portfolio/lots/1') && init?.method === 'DELETE');
      expect(call).toBeTruthy();
    });
  });
});
