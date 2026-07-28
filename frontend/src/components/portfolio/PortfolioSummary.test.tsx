import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import PortfolioSummary from './PortfolioSummary';
import { portfolioTotals, renderWithProviders } from '../../testUtils';

describe('PortfolioSummary', () => {
  it('renders totals with gain/loss formatting', () => {
    renderWithProviders(<PortfolioSummary totals={portfolioTotals()} />);
    expect(screen.getByText('Total Investment')).toBeInTheDocument();
    expect(screen.getByText('$300.00')).toBeInTheDocument();
    expect(screen.getByText('$350.00')).toBeInTheDocument();
    expect(screen.getByText('16.67%')).toBeInTheDocument();
  });

  it('renders a negative gain/loss', () => {
    renderWithProviders(
      <PortfolioSummary totals={portfolioTotals({ total_gain_loss: -50, total_gain_loss_pct: -5 })} />,
    );
    expect(screen.getByText('-$50.00')).toBeInTheDocument();
    expect(screen.getByText('-5.00%')).toBeInTheDocument();
  });

  it('renders — when dollars per day is null', () => {
    renderWithProviders(<PortfolioSummary totals={portfolioTotals({ total_dollars_per_day: null })} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
