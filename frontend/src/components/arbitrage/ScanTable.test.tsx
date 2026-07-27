import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import ScanTable from './ScanTable';
import { renderWithProviders } from '../../testUtils';
import type { ScanRow } from '../../api/arbitrage';

function row(overrides: Partial<ScanRow> = {}): ScanRow {
  return {
    security: 'MSTR',
    name: 'Strategy',
    kind: 'nav_vehicle',
    underlying: 'BTC-USD',
    score: 9.7,
    verdict: 'reject',
    basis: 'nav_discount',
    discount_pct: -16.92,
    spread_zscore: -1.35,
    half_life_days: 66.4,
    cointegrated: false,
    convergence: 'buyback',
    hedge_instrument: 'IBIT',
    hedge_available: true,
    breaks_on: ['Negative carry (senior claims serviced out of NAV)'],
    ...overrides,
  };
}

describe('ScanTable', () => {
  it('renders score, verdict and the net discount', () => {
    renderWithProviders(<ScanTable rows={[row()]} />);
    expect(screen.getByText('MSTR')).toBeInTheDocument();
    expect(screen.getByText('9.7')).toBeInTheDocument();
    expect(screen.getByText('reject')).toBeInTheDocument();
    expect(screen.getByText('-16.92%')).toBeInTheDocument();
    expect(screen.getByText('nav vehicle')).toBeInTheDocument();
  });

  it('marks an unhedgeable leg instead of naming a futures contract', () => {
    renderWithProviders(
      <ScanTable
        rows={[row({ security: 'GLD', hedge_available: false, hedge_instrument: null })]}
      />,
    );
    expect(screen.getByText('futures only')).toBeInTheDocument();
  });

  it('shows the first risk with an overflow count', () => {
    renderWithProviders(
      <ScanTable rows={[row({ breaks_on: ['Negative carry', 'Spread trending wider'] })]} />,
    );
    expect(screen.getByText('Negative carry')).toBeInTheDocument();
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  it('dashes out a row with no discount and no risks', () => {
    renderWithProviders(
      <ScanTable rows={[row({ discount_pct: null, breaks_on: [], score: null })]} />,
    );
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it('reports the clicked security', () => {
    const onSelect = vi.fn();
    renderWithProviders(<ScanTable rows={[row()]} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('MSTR'));
    expect(onSelect).toHaveBeenCalledWith('MSTR');
  });

  it('is inert without a select handler', () => {
    renderWithProviders(<ScanTable rows={[row()]} />);
    fireEvent.click(screen.getByText('MSTR'));
    expect(screen.getByText('MSTR')).toBeInTheDocument();
  });
});
