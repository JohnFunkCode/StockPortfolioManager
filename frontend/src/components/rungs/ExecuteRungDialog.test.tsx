import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import ExecuteRungDialog from './ExecuteRungDialog';
import { mockApi, renderWithProviders, type MockedApi } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function numberInputs() {
  return Array.from(document.querySelectorAll('input[type="number"]')) as HTMLInputElement[];
}

function executeBody(api: MockedApi) {
  const call = api.calls.find(([url, init]) => url.includes('/execute') && init?.method === 'POST');
  return call ? JSON.parse(String(call[1]?.body)) : null;
}

function mount(onClose = vi.fn()) {
  const api = mockApi([['/api/rungs/4/execute', { rung_id: 4, status: 'EXECUTED' }]]);
  renderWithProviders(
    <ExecuteRungDialog open rungId={4} sharesSoldPlanned={10} targetPrice={120} onClose={onClose} />,
  );
  return { api, onClose };
}

describe('ExecuteRungDialog', () => {
  it('submits an execution', async () => {
    const { api } = mount();
    fireEvent.click(screen.getByRole('button', { name: /Record Execution/i }));
    await waitFor(() => expect(executeBody(api)).not.toBeNull());
  });

  it('prefills from the plan and submits those defaults', async () => {
    const { api } = mount();
    const nums = numberInputs();
    expect(nums[0].value).toBe('120.00');  // target price
    expect(nums[1].value).toBe('10');      // planned shares

    fireEvent.click(screen.getByRole('button', { name: /Record Execution/i }));
    await waitFor(() => expect(executeBody(api)).not.toBeNull());
    // Zero tax and blank notes are omitted rather than sent as 0/''.
    expect(executeBody(api)).toEqual({ executed_price: 120, shares_sold: 10 });
  });

  it('sends edited values including tax and notes', async () => {
    const { api } = mount();
    const nums = numberInputs();
    fireEvent.change(nums[0], { target: { value: '124.5' } });
    fireEvent.change(nums[1], { target: { value: '8' } });
    fireEvent.change(nums[2], { target: { value: '31.25' } });
    fireEvent.change(document.querySelector('textarea') as HTMLTextAreaElement, {
      target: { value: 'partial fill' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Record Execution/i }));

    await waitFor(() => expect(executeBody(api)).not.toBeNull());
    expect(executeBody(api)).toEqual({
      executed_price: 124.5,
      shares_sold: 8,
      tax_paid: 31.25,
      notes: 'partial fill',
    });
  });

  it('closes once the execution is recorded', async () => {
    const { onClose } = mount();
    fireEvent.click(screen.getByRole('button', { name: /Record Execution/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('stays open when the request fails', async () => {
    const onClose = vi.fn();
    mockApi([['/api/rungs/4/execute', () => ({ __status: 500, error: 'rung already executed' })]]);
    renderWithProviders(
      <ExecuteRungDialog open rungId={4} sharesSoldPlanned={10} targetPrice={120} onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Record Execution/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('cancel closes without submitting', () => {
    const { api, onClose } = mount();
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(onClose).toHaveBeenCalled();
    expect(api.calls).toHaveLength(0);
  });
});
