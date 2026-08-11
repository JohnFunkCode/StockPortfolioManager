import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import CreatePlanDialog from './CreatePlanDialog';
import { mockApi, renderWithProviders, securityRow, type MockedApi } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

/** GET /api/portfolio — the holdings the symbol picker is narrowed to (#147 H5). */
function holdings(...symbols: string[]): [string, object] {
  return ['/api/portfolio', { securities: symbols.map((s) => securityRow(s, { source: 'portfolio' })) }];
}

/** The dialog's plain text inputs. In picker mode the symbol field is a MUI
 *  Select, whose native input carries no `type`, so this is template name only. */
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

/** Drive the MUI Select: wait for the holdings to land, open it, pick an option. */
async function pickSymbol(symbol: string) {
  const combo = await screen.findByRole('combobox');
  await waitFor(() => expect(combo).not.toHaveAttribute('aria-disabled', 'true'));
  fireEvent.mouseDown(combo);
  fireEvent.click(await screen.findByRole('option', { name: symbol }));
}

describe('CreatePlanDialog', () => {
  it('is hidden when closed', () => {
    mockApi([]);
    renderWithProviders(<CreatePlanDialog open={false} onClose={() => {}} onCreated={() => {}} />);
    expect(screen.queryByText(/Create.*Plan/i)).toBeNull();
  });

  it('does not fetch holdings while it is closed', async () => {
    const api = mockApi([holdings('INTC')]);
    renderWithProviders(<CreatePlanDialog open={false} onClose={() => {}} onCreated={() => {}} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.calls).toHaveLength(0);
  });

  it('offers only the symbols the caller holds', async () => {
    mockApi([holdings('WMT', 'INTC'), ['/api/plans', { instance_id: 1 }]]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);

    const combo = await screen.findByRole('combobox');
    await waitFor(() => expect(combo).not.toHaveAttribute('aria-disabled', 'true'));
    fireEvent.mouseDown(combo);

    const options = (await screen.findAllByRole('option')).map((o) => o.textContent);
    expect(options).toEqual(['INTC', 'WMT']); // sorted, and nothing they don't hold
  });

  it('pre-selects the symbol it was opened for', async () => {
    mockApi([holdings('INTC', 'WMT')]);
    renderWithProviders(
      <CreatePlanDialog open initialSymbol="WMT" onClose={() => {}} onCreated={() => {}} />,
    );
    expect(await screen.findByText('WMT')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Create/i })).toBeEnabled();
  });

  it('does not show the pre-selection before its option exists', async () => {
    // The holdings land after `initialSymbol` is seeded, so a naive value would
    // spend the opening frames out of range: MUI warns, and the field is blank.
    const warn = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockApi([holdings('INTC', 'WMT')]);
    renderWithProviders(
      <CreatePlanDialog open initialSymbol="WMT" onClose={() => {}} onCreated={() => {}} />,
    );

    expect(screen.getByRole('button', { name: /^Create/i })).toBeDisabled();
    await screen.findByText('WMT'); // …and it arrives once the option does
    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/out-of-range/i);
    warn.mockRestore();
  });

  it('stays blank for a pre-selection the caller does not hold', async () => {
    // Nothing in the picker to show, and submitting it would only earn the 422.
    // A disabled button beats a blank field that posts an invisible symbol.
    mockApi([holdings('INTC')]);
    renderWithProviders(
      <CreatePlanDialog open initialSymbol="WMT" onClose={() => {}} onCreated={() => {}} />,
    );

    const combo = await screen.findByRole('combobox');
    await waitFor(() => expect(combo).not.toHaveAttribute('aria-disabled', 'true'));
    expect(screen.queryByText('WMT')).toBeNull();
    expect(screen.getByRole('button', { name: /^Create/i })).toBeDisabled();
  });

  it('submits a new plan', async () => {
    const api = mockApi([holdings('INTC'), ['/api/plans', { instance_id: 3, symbol: 'INTC' }]]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);
    await pickSymbol('INTC');
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));
    await waitFor(() => expect(postBody(api)).not.toBeNull());
    expect(postBody(api).symbol).toBe('INTC');
  });

  it('parses the advanced parameters', async () => {
    const api = mockApi([holdings('INTC'), ['/api/plans', { instance_id: 3 }]]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);

    await pickSymbol('INTC');
    fireEvent.change(textInputs()[0], { target: { value: 'Aggressive' } });
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
    mockApi([holdings('WMT'), ['/api/plans', { instance_id: 42 }]]);
    const onClose = vi.fn();
    const onCreated = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={onCreated} />);
    await pickSymbol('WMT');
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(42));
    expect(onClose).toHaveBeenCalled();
  });

  it('does not call onCreated when the response carries no instance_id', async () => {
    mockApi([holdings('WMT'), ['/api/plans', { ok: true }]]);
    const onClose = vi.fn();
    const onCreated = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={onCreated} />);
    await pickSymbol('WMT');
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('surfaces a build failure without closing', async () => {
    mockApi([holdings('INTC'), ['/api/plans', () => ({ __status: 500, error: 'not enough history' })]]);
    const onClose = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={() => {}} />);
    await pickSymbol('INTC');
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(screen.getByText(/not enough history/i)).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("surfaces the server's holding-invariant refusal", async () => {
    // The picker is the polite half; this 422 is the enforcement (#147 Part H5)
    // and has to reach the user, not disappear into a console.
    mockApi([
      holdings('INTC'),
      ['/api/plans', () => ({ __status: 422, error: 'No open position in INTC for john' })],
    ]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);
    await pickSymbol('INTC');
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));

    await waitFor(() => expect(screen.getByText(/No open position in INTC/i)).toBeInTheDocument());
  });

  it('keeps Create disabled until a symbol is picked', async () => {
    mockApi([holdings('INTC')]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);
    const create = screen.getByRole('button', { name: /^Create/i });
    expect(create).toBeDisabled();
    await pickSymbol('INTC');
    expect(create).toBeEnabled();
  });

  it('says so plainly when there is nothing to build a ladder on', async () => {
    mockApi([['/api/portfolio', { securities: [] }]]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No open positions/i)).toBeInTheDocument());
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Create/i })).toBeDisabled();
  });

  it('degrades to free text when the portfolio cannot be read', async () => {
    // We have no evidence about the invariant, so we must not enforce it here —
    // the server still will. Blocking creation would be us guessing.
    const api = mockApi([
      ['/api/portfolio', () => ({ __status: 500, error: 'portfolio down' })],
      ['/api/plans', { instance_id: 7 }],
    ]);
    renderWithProviders(<CreatePlanDialog open onClose={() => {}} onCreated={() => {}} />);

    await waitFor(() => expect(screen.getByText(/Couldn't read your portfolio/i)).toBeInTheDocument());
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Symbol/i), { target: { value: '  intc  ' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create/i }));
    await waitFor(() => expect(postBody(api)).not.toBeNull());
    expect(postBody(api).symbol).toBe('INTC'); // still normalised
  });

  it('cancel closes without submitting', async () => {
    const api = mockApi([holdings('INTC'), ['/api/plans', { instance_id: 1 }]]);
    const onClose = vi.fn();
    renderWithProviders(<CreatePlanDialog open onClose={onClose} onCreated={() => {}} />);
    await screen.findByRole('combobox');
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(onClose).toHaveBeenCalled();
    expect(postBody(api)).toBeNull();
  });
});
