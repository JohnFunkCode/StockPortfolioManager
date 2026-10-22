/** OptionsRefreshPanel (moved from the retired Securities page, #224). */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import OptionsRefreshPanel from './OptionsRefreshPanel';
import { mockApi, renderWithProviders } from '../../testUtils';

const ROUTE = '/api/securities/refresh-options-snapshots';

afterEach(() => vi.unstubAllGlobals());

describe('OptionsRefreshPanel', () => {
  it('refreshes every security on the ATM chain', async () => {
    const api = mockApi([[ROUTE, { total: 40, succeeded: 40, failed: 0, duration_seconds: 12.5 }]]);
    renderWithProviders(<OptionsRefreshPanel />);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh All (ATM)' }));

    expect(await screen.findByText('40/40 succeeded in 12.5s')).toBeInTheDocument();
    expect(screen.queryByText(/failed/)).toBeNull();
    const [url, init] = api.calls[0];
    expect(url).toContain('source=all');
    expect(url).toContain('chain_type=atm');
    expect(init?.method).toBe('POST');
  });

  it('refreshes the portfolio on the full chain and reports failures', async () => {
    const api = mockApi([[ROUTE, { total: 5, succeeded: 3, failed: 2, duration_seconds: 30 }]]);
    renderWithProviders(<OptionsRefreshPanel />);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Portfolio (Full Chain)' }));

    expect(await screen.findByText('3/5 succeeded in 30s')).toBeInTheDocument();
    expect(screen.getByText('· 2 failed')).toBeInTheDocument();
    expect(api.calls[0][0]).toContain('source=portfolio');
    expect(api.calls[0][0]).toContain('chain_type=full');
  });

  it('shows an error alert when the refresh fails', async () => {
    mockApi([[ROUTE, { __status: 500, detail: 'boom' }]]);
    renderWithProviders(<OptionsRefreshPanel />);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh All (ATM)' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
