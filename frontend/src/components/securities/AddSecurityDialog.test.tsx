import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import AddSecurityDialog from './AddSecurityDialog';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

function symbolInput() {
  // The Symbol field is the first text input in the dialog.
  return document.querySelectorAll('input[type="text"]')[0] as HTMLInputElement;
}

describe('AddSecurityDialog', () => {
  it('does not render its title when closed', () => {
    mockApi([]);
    renderWithProviders(<AddSecurityDialog open={false} onClose={() => {}} />);
    expect(screen.queryByText('Add Security')).toBeNull();
  });

  it('submits a new watchlist entry', async () => {
    const api = mockApi([
      ['/api/securities/lookup', { symbol: 'INTC', name: 'Intel' }],
      ['/api/watchlist', { added: true }],
    ]);
    const onClose = vi.fn();
    renderWithProviders(<AddSecurityDialog open onClose={onClose} />);

    fireEvent.change(symbolInput(), { target: { value: 'INTC' } });
    fireEvent.click(screen.getByText(/Add to Watchlist/i));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const post = api.calls.find((c) => c[0].includes('/api/watchlist') && c[1]?.method === 'POST');
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post![1]?.body)).symbol).toBe('INTC');
  });

  it('submits a portfolio entry with cost basis when the Portfolio tab is chosen', async () => {
    const api = mockApi([
      ['/api/securities/lookup', { symbol: 'INTC', name: 'Intel', suggested_tags: [] }],
      ['/api/portfolio', { added: true }],
    ]);
    const onClose = vi.fn();
    renderWithProviders(<AddSecurityDialog open onClose={onClose} />);

    // Switch to the Portfolio destination tab.
    fireEvent.click(screen.getByRole('tab', { name: 'Portfolio' }));
    fireEvent.change(symbolInput(), { target: { value: 'INTC' } });
    fireEvent.click(screen.getByText(/Add to Portfolio/i));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const post = api.calls.find((c) => c[0].includes('/api/portfolio') && c[1]?.method === 'POST');
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post![1]?.body)).symbol).toBe('INTC');
  });

  it('offers no currency field for a watchlist entry, and sends none', async () => {
    // The server resolves the watchlist currency off the exchange; a picker
    // here would only let a user contradict it, which is how three European
    // names in watchlist.yaml came to be labelled USD.
    const api = mockApi([
      ['/api/securities/lookup', { symbol: 'ASSA-B.ST', name: 'Assa Abloy' }],
      ['/api/watchlist', { added: true }],
    ]);
    const onClose = vi.fn();
    renderWithProviders(<AddSecurityDialog open onClose={onClose} />);

    // The Currency <Select> is the dialog's only combobox.
    expect(screen.queryByRole('combobox')).toBeNull();

    fireEvent.change(symbolInput(), { target: { value: 'ASSA-B.ST' } });
    fireEvent.click(screen.getByText(/Add to Watchlist/i));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const post = api.calls.find((c) => c[0].includes('/api/watchlist') && c[1]?.method === 'POST');
    expect(JSON.parse(String(post![1]?.body))).not.toHaveProperty('currency');
  });

  it('still offers currency on the Portfolio tab, where the caller sets it', async () => {
    const api = mockApi([
      ['/api/securities/lookup', { symbol: 'INTC', name: 'Intel', suggested_tags: [] }],
      ['/api/portfolio', { added: true }],
    ]);
    const onClose = vi.fn();
    renderWithProviders(<AddSecurityDialog open onClose={onClose} />);

    fireEvent.click(screen.getByRole('tab', { name: 'Portfolio' }));
    expect(screen.getByRole('combobox')).toHaveTextContent('USD');

    fireEvent.change(symbolInput(), { target: { value: 'INTC' } });
    fireEvent.click(screen.getByText(/Add to Portfolio/i));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const post = api.calls.find((c) => c[0].includes('/api/portfolio') && c[1]?.method === 'POST');
    expect(JSON.parse(String(post![1]?.body)).currency).toBe('USD');
  });

  it('adds tag chips on Enter', async () => {
    mockApi([['/api/securities/lookup', { symbol: 'INTC', name: 'Intel', suggested_tags: [] }]]);
    renderWithProviders(<AddSecurityDialog open onClose={() => {}} />);
    const tagField = screen.getByLabelText(/Tags/i).querySelector('input')
      ?? document.querySelectorAll('input[type="text"]')[document.querySelectorAll('input[type="text"]').length - 1];
    fireEvent.change(tagField as HTMLInputElement, { target: { value: 'chips' } });
    fireEvent.keyDown(tagField as HTMLInputElement, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('chips')).toBeInTheDocument());
  });

  it('blocks submission without a symbol', () => {
    mockApi([]);
    renderWithProviders(<AddSecurityDialog open onClose={() => {}} />);
    // With an empty symbol the primary button is disabled.
    const addButton = screen.getByText(/Add to Watchlist/i).closest('button')!;
    expect(addButton).toBeDisabled();
  });
});
