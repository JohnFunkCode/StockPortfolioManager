import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';

import SectorBreakdownPanel from './SectorBreakdownPanel';
import { mockApi, renderWithProviders, sectorBreakdown, sectorRanking } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const SECTORS = '/api/securities/fundamentals/sector-breakdown';

describe('SectorBreakdownPanel', () => {
  it('shows a spinner while the request is in flight', () => {
    mockApi([[SECTORS, sectorBreakdown()]]);
    renderWithProviders(<SectorBreakdownPanel />);
    expect(screen.getByText(/Grouping by sector/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([[SECTORS, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<SectorBreakdownPanel />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load the sector breakdown/i)).toBeInTheDocument(),
    );
  });

  it('renders one card per sector with its ranked symbols', async () => {
    mockApi([
      [
        SECTORS,
        sectorBreakdown({
          Technology: [
            sectorRanking('INTC', { composite_score: 72 }),
            sectorRanking('AVGO', { rank: 2, composite_score: 68 }),
          ],
          Energy: [sectorRanking('XOM', { composite_score: 55, fundamental_label: 'mixed' })],
        }),
      ],
    ]);
    renderWithProviders(<SectorBreakdownPanel />);
    await waitFor(() => expect(screen.getByTestId('sector-breakdown-panel')).toBeInTheDocument());

    const tech = screen.getByTestId('sector-card-Technology');
    expect(within(tech).getByText('INTC')).toBeInTheDocument();
    expect(within(tech).getByText('72')).toBeInTheDocument();
    expect(within(tech).getByText('AVGO')).toBeInTheDocument();
    expect(within(screen.getByTestId('sector-card-Energy')).getByText('XOM')).toBeInTheDocument();
    expect(screen.getByText('2 sectors · 3 tracked')).toBeInTheDocument();
  });

  it('renders the Unknown bucket rather than dropping unclassified symbols', async () => {
    // Its size is the signal about how complete the cache is.
    mockApi([[SECTORS, sectorBreakdown({ Unknown: [sectorRanking('NEWCO')] })]]);
    renderWithProviders(<SectorBreakdownPanel />);
    await waitFor(() =>
      expect(screen.getByTestId('sector-card-Unknown')).toBeInTheDocument(),
    );
    expect(screen.getByText('NEWCO')).toBeInTheDocument();
  });

  it('explains an empty breakdown', async () => {
    mockApi([[SECTORS, sectorBreakdown({})]]);
    renderWithProviders(<SectorBreakdownPanel />);
    await waitFor(() =>
      expect(screen.getByText(/nothing tracked has a cached score yet/i)).toBeInTheDocument(),
    );
  });

  it('dashes out a missing score rather than printing a zero', async () => {
    mockApi([
      [
        SECTORS,
        sectorBreakdown({
          Technology: [
            sectorRanking('NEW', { composite_score: null, fundamental_label: null }),
          ],
        }),
      ],
    ]);
    renderWithProviders(<SectorBreakdownPanel />);
    await waitFor(() => expect(screen.getByText('NEW')).toBeInTheDocument());
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('sends the tracked scope on the wire', async () => {
    const api = mockApi([[SECTORS, sectorBreakdown()]]);
    renderWithProviders(<SectorBreakdownPanel />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(api.calls[0][0]).toContain('scope=tracked');
  });
});
