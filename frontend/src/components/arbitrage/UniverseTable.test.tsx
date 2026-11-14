import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import UniverseTable from './UniverseTable';
import { renderWithProviders } from '../../testUtils';
import type { UniverseEntry } from '../../api/arbitrage';

function entry(overrides: Partial<UniverseEntry> = {}): UniverseEntry {
  return {
    security: 'MSTR',
    name: 'Strategy (MicroStrategy)',
    kind: 'nav_vehicle',
    underlying: 'BTC-USD',
    hedge_instrument: 'IBIT',
    convergence_mechanism: 'buyback',
    holdings_units: 843775,
    holdings_as_of: '2026-07-14',
    senior_claims_usd: 16_500_000_000,
    annual_senior_cost_usd: 854_000_000,
    other_assets_usd: 0,
    diluted_shares: 350_000_000,
    source: '10-Q Q1 FY2026 + The Block treasury tracker',
    notes: 'Converts plus four preferred series.',
    holdings_age_days: 122,
    holdings_stale: true,
    hedge_available: true,
    ...overrides,
  };
}

const USO = entry({
  security: 'USO',
  name: 'United States Oil Fund',
  kind: 'commodity_etf',
  underlying: 'CL=F',
  hedge_instrument: null,
  hedge_available: false,
  convergence_mechanism: 'redemption',
  holdings_units: null,
  holdings_as_of: null,
  holdings_age_days: null,
  holdings_stale: false,
  senior_claims_usd: 0,
  diluted_shares: null,
  source: null,
  notes: 'Roll yield decays the fund against spot.',
});

describe('UniverseTable', () => {
  it('renders one row per curated pair with its family, hedge and mechanism', () => {
    renderWithProviders(<UniverseTable entries={[entry(), USO]} />);
    expect(screen.getByText('MSTR')).toBeInTheDocument();
    expect(screen.getByText('nav vehicle')).toBeInTheDocument();
    expect(screen.getByText('BTC-USD')).toBeInTheDocument();
    expect(screen.getByText('IBIT')).toBeInTheDocument();
    expect(screen.getByText('buyback')).toBeInTheDocument();
    expect(screen.getByText('USO')).toBeInTheDocument();
    expect(screen.getByText('commodity etf')).toBeInTheDocument();
  });

  it('flags stale holdings with their age', () => {
    renderWithProviders(<UniverseTable entries={[entry()]} />);
    expect(screen.getByText('2026-07-14')).toBeInTheDocument();
    expect(screen.getByText('122 days old')).toBeInTheDocument();
  });

  it('shows no age chip while the holdings are fresh', () => {
    renderWithProviders(
      <UniverseTable entries={[entry({ holdings_age_days: 12, holdings_stale: false })]} />,
    );
    expect(screen.getByText('2026-07-14')).toBeInTheDocument();
    expect(screen.queryByText(/days old/)).toBeNull();
  });

  it('shows no age chip when a stale entry arrives without an age', () => {
    renderWithProviders(
      <UniverseTable entries={[entry({ holdings_age_days: null, holdings_stale: true })]} />,
    );
    expect(screen.getByText('2026-07-14')).toBeInTheDocument();
    expect(screen.queryByText(/days old/)).toBeNull();
  });

  it('marks a futures-only hedge and dashes out missing holdings', () => {
    renderWithProviders(<UniverseTable entries={[USO]} />);
    expect(screen.getByText('futures only')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('expands a row to the rationale and the NAV capital figures', () => {
    renderWithProviders(<UniverseTable entries={[entry()]} />);
    expect(screen.queryByText('Why this link exists')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Show details for MSTR' }));

    expect(screen.getByText('Converts plus four preferred series.')).toBeInTheDocument();
    expect(screen.getByText('Units of BTC-USD held')).toBeInTheDocument();
    expect(screen.getByText('843,775')).toBeInTheDocument();
    expect(screen.getByText('$16.5B')).toBeInTheDocument();
    expect(screen.getByText('350M')).toBeInTheDocument();
    expect(screen.getByText('10-Q Q1 FY2026 + The Block treasury tracker')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide details for MSTR' })).toBeInTheDocument();
  });

  it('shows the capital figures for NAV vehicles only', () => {
    renderWithProviders(<UniverseTable entries={[USO]} />);
    fireEvent.click(screen.getByText('USO'));
    expect(screen.getByText('Roll yield decays the fund against spot.')).toBeInTheDocument();
    expect(screen.queryByTestId('universe-capital-USO')).toBeNull();
    expect(screen.queryByText('Senior claims')).toBeNull();
  });

  it('says so when a pair has no recorded rationale', () => {
    renderWithProviders(<UniverseTable entries={[entry({ notes: null })]} />);
    fireEvent.click(screen.getByText('MSTR'));
    expect(screen.getByText('No rationale recorded for this pair.')).toBeInTheDocument();
  });

  it('dashes out capital figures the curator left blank', () => {
    renderWithProviders(
      <UniverseTable
        entries={[entry({ holdings_units: null, senior_claims_usd: null, diluted_shares: null, source: null })]}
      />,
    );
    fireEvent.click(screen.getByText('MSTR'));
    expect(screen.getAllByText('—')).toHaveLength(4);
  });

  it('opens one row at a time', () => {
    renderWithProviders(<UniverseTable entries={[entry(), USO]} />);
    fireEvent.click(screen.getByText('MSTR'));
    fireEvent.click(screen.getByText('USO'));
    expect(screen.getByRole('button', { name: 'Show details for MSTR' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide details for USO' })).toBeInTheDocument();
  });

  it('reports the pair whose workup was asked for', () => {
    const onOpenWorkup = vi.fn();
    renderWithProviders(<UniverseTable entries={[entry()]} onOpenWorkup={onOpenWorkup} />);
    fireEvent.click(screen.getByText('MSTR'));
    fireEvent.click(screen.getByRole('button', { name: /Open workup/ }));
    expect(onOpenWorkup).toHaveBeenCalledWith('MSTR');
  });

  it('offers no workup link without a handler', () => {
    renderWithProviders(<UniverseTable entries={[entry()]} />);
    fireEvent.click(screen.getByText('MSTR'));
    expect(screen.queryByRole('button', { name: /Open workup/ })).toBeNull();
  });

  it('says so when nothing is configured', () => {
    renderWithProviders(<UniverseTable entries={[]} />);
    expect(screen.getByText('No curated pairs are configured.')).toBeInTheDocument();
  });
});
