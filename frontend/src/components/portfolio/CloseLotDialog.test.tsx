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

// Sell is disabled until the preview has answered for the typed share count.
async function clickSell() {
  const sell = screen.getByRole('button', { name: 'Sell' });
  await waitFor(() => expect(sell).toBeEnabled());
  fireEvent.click(sell);
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
    await clickSell();
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    expect(closeBody(api)).toMatchObject({ shares: 10, sale_price: 35 });
  });

  it('closes once the sale succeeds', async () => {
    const { onClose } = mount();
    await clickSell();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('rejects a zero share count', () => {
    mount();
    const nums = document.querySelectorAll('input[type="number"]') as NodeListOf<HTMLInputElement>;
    fireEvent.change(nums[0], { target: { value: '0' } });
    // Nothing to preview for zero shares, so Sell stays enabled and validation speaks.
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    expect(screen.getByText('Shares must be greater than zero.')).toBeInTheDocument();
  });

  it('rejects a zero sale price', async () => {
    mount();
    const nums = document.querySelectorAll('input[type="number"]') as NodeListOf<HTMLInputElement>;
    fireEvent.change(nums[1], { target: { value: '0' } });
    await clickSell();
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
    await clickSell();
    await waitFor(() => expect(closeBody(api)).not.toBeNull());
    const body = closeBody(api);
    expect(body).toMatchObject({ shares: 10, sale_price: 35, notes: 'Hit my target' });
    expect(body).not.toHaveProperty('lot_notes');
  });

  it('sends neither notes nor lot_notes when the reason is blank', async () => {
    const { api } = mount();
    await clickSell();
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
    await clickSell();
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

    await clickSell();
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

  describe('Sell waits for a preview that matches the typed share count', () => {
    it('holds Sell back while the first preview is still pending', async () => {
      mount();
      expect(screen.getByRole('button', { name: 'Sell' })).toBeDisabled();
      expect(screen.getByText('Checking which lots this sale covers…')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByRole('button', { name: 'Sell' })).toBeEnabled());
      expect(screen.queryByText('Checking which lots this sale covers…')).not.toBeInTheDocument();
    });

    it('cannot submit a one-lot sale grown into a multi-lot sale before the preview settles', async () => {
      // Up to 6 shares fit in one lot; beyond that the sale spans two.
      const api = mockApi([
        [
          '/api/portfolio/lots/1/close/preview',
          (_url: string, init?: RequestInit) => {
            const { shares } = JSON.parse(String(init?.body));
            return { symbol: 'INTC', allocations: shares > 6 ? TWO_LOTS : ONE_LOT };
          },
        ],
        ['/api/portfolio/lots/1/close', { lot: lotRow({ status: 'CLOSED' }) }],
      ]);
      renderWithProviders(<CloseLotDialog open lot={lotRow()} onClose={vi.fn()} />);
      const sell = screen.getByRole('button', { name: 'Sell' });
      const sharesInput = document.querySelectorAll('input[type="number"]')[0] as HTMLInputElement;

      // A one-lot sale: once its preview settles, Sell opens up and no per-lot choice is offered.
      fireEvent.change(sharesInput, { target: { value: '4' } });
      await waitFor(() => expect(sell).toBeEnabled());
      expect(screen.queryByLabelText('Same note for all lots')).not.toBeInTheDocument();

      // Grow it into a multi-lot sale. Sell closes at once, before the preview catches up,
      // and a click in that window must not file a sale.
      fireEvent.change(sharesInput, { target: { value: '10' } });
      expect(sell).toBeDisabled();
      fireEvent.click(sell);
      expect(closeBody(api)).toBeNull();

      // When the preview settles the per-lot choice appears and the sale can go through.
      expect(await screen.findByLabelText('Same note for all lots')).toBeInTheDocument();
      await waitFor(() => expect(sell).toBeEnabled());
      fireEvent.click(sell);
      await waitFor(() => expect(closeBody(api)).not.toBeNull());
      expect(closeBody(api)).toMatchObject({ shares: 10 });
    });

    it('blocks Sell and offers a retry when the preview fails', async () => {
      const api = mockApi([
        ['/api/portfolio/lots/1/close/preview', () => ({ __status: 500, error: 'preview exploded' })],
        ['/api/portfolio/lots/1/close', { lot: lotRow({ status: 'CLOSED' }) }],
      ]);
      renderWithProviders(<CloseLotDialog open lot={lotRow()} onClose={vi.fn()} />);

      expect(await screen.findByText(/Couldn't check which lots this sale covers/)).toBeInTheDocument();
      const sell = screen.getByRole('button', { name: 'Sell' });
      expect(sell).toBeDisabled();
      fireEvent.click(sell);
      expect(closeBody(api)).toBeNull();
      expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    });

    it('lets the sale through once a retry succeeds', async () => {
      let failing = true;
      const api = mockApi([
        [
          '/api/portfolio/lots/1/close/preview',
          () => (failing ? { __status: 500, error: 'preview exploded' } : { symbol: 'INTC', allocations: ONE_LOT }),
        ],
        ['/api/portfolio/lots/1/close', { lot: lotRow({ status: 'CLOSED' }) }],
      ]);
      renderWithProviders(<CloseLotDialog open lot={lotRow()} onClose={vi.fn()} />);
      const retry = await screen.findByRole('button', { name: 'Retry' });
      failing = false;
      fireEvent.click(retry);

      await waitFor(() => expect(screen.getByRole('button', { name: 'Sell' })).toBeEnabled());
      expect(screen.queryByText(/Couldn't check which lots/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
      await waitFor(() => expect(closeBody(api)).not.toBeNull());
    });
  });
});
