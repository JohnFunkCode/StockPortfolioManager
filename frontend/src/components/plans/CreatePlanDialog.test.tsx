import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import CreatePlanDialog from './CreatePlanDialog';
import { mockApi, renderWithProviders, type MockedApi } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

/** The dialog's text inputs, in render order: symbol, then template name. */
function textInputs() {
  return Array.from(document.querySelectorAll('input[type="text"]')) as HTMLInputElement[];
}

function numberInputs() {
  return Array.from(document.querySelectorAll('input[type="number"]')) as HTMLInputElement[];
}

function postBody(api: MockedApi) {
  const call = api.calls.find(([url, init]) => url.includes('/api/plans') && init?.method === 'POST');
  return call ? JSON.parse(String(call[1]?.body)) : null;
}

describe('CreatePlanDialog', () => {
  it('is hidden when closed', () => {
    mockApi([]);
    renderWithProviders(<CreatePlanDialog open={false} onClose={() => {}} onCreated={() => {}} />);
    expect(screen.queryByText(/Create.*Plan/i)).toBeNull();
  });

  it('submits a new plan', async () => {
    const api = mockApi([['/api/plans', { instance_id: 3, symbol: 'INTC' }]]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(textInputs()[0], { target: { value: 'INTC' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));
    await waitFor(() => expect(postBody(api)).not.toBeNull());
  });

  it('normalises the symbol and parses the advanced parameters', async () => {
    const api = mockApi([['/api/plans', { instance_id: 3 }]]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);

    fireEvent.change(textInputs()[0], { target: { value: '  intc  ' } });
    fireEvent.change(textInputs()[1], { target: { value: 'Aggressive' } });
    const nums = numberInputs();
    fireEvent.change(nums[0], { target: { value: '180' } });   // history window
    fireEvent.change(nums[1], { target: { value: '6' } });     // iterations
    fireEvent.change(nums[2], { target: { value: '0.7' } });   // alpha
    fireEvent.change(nums[3], { target: { value: '0.02' } });  // min H
    fireEvent.change(nums[4], { target: { value: '0.40' } });  // max H
    fireEvent.change(nums[5], { target: { value: '500' } });   // max S0
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(postBody(api)).not.toBeNull());
    expect(postBody(api)).toEqual({
      symbol: 'INTC',
      template_name: 'Aggressive',
      params: {
        history_window_days: 180,
        n_iterations: 6,
        alpha: 0.7,
        min_H: 0.02,
        max_H: 0.4,
        max_s0: 500,
      },
    });
  });

  it('closes and reports the new id on success', async () => {
    mockApi([['/api/plans', { instance_id: 42 }]]);
    const onClose = vi.fn();
    const onCreated = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={onCreated} />);
    fireEvent.change(textInputs()[0], { target: { value: 'WMT' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(42));
    expect(onClose).toHaveBeenCalled();
  });

  it('does not call onCreated when the response carries no instance_id', async () => {
    mockApi([['/api/plans', { ok: true }]]);
    const onClose = vi.fn();
    const onCreated = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={onCreated} />);
    fireEvent.change(textInputs()[0], { target: { value: 'WMT' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('surfaces a build failure without closing', async () => {
    mockApi([['/api/plans', () => ({ __status: 500, error: 'not enough history' })]]);
    const onClose = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={() => {}} />);
    fireEvent.change(textInputs()[0], { target: { value: 'INTC' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(screen.getByText(/not enough history/i)).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('keeps Create disabled until a symbol is entered', () => {
    mockApi([]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);
    const create = screen.getByRole('button', { name: /^Create/i });
    expect(create).toBeDisabled();
    fireEvent.change(textInputs()[0], { target: { value: '   ' } });
    expect(create).toBeDisabled();
    fireEvent.change(textInputs()[0], { target: { value: 'INTC' } });
    expect(create).toBeEnabled();
  });

  it('cancel closes without submitting', () => {
    const api = mockApi([['/api/plans', { instance_id: 1 }]]);
    const onClose = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(onClose).toHaveBeenCalled();
    expect(api.calls).toHaveLength(0);
  });
});
